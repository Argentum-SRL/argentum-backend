# -*- coding: utf-8 -*-
"""
app/services/categoria_busqueda_service.py — Búsqueda local de categorías por catálogo y memoria.
"""
from __future__ import annotations

import re
from typing import Any
from uuid import UUID

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.core.catalogo_categorias import ARBOL_CATEGORIAS, resolver_alias
from app.models.memoria_comercio import MemoriaComercio
from app.services.memoria_comercio_service import clave_comercio
from app.utils.texto import normalizar_texto

MAX_RESULTADOS_CATALOGO = 8
MAX_RESULTADOS_MEMORIA = 3


def normalizar_busqueda(texto: str | None) -> str:
    """Normaliza un texto de búsqueda: sin acentos, sin signos y espacios simples."""
    if not texto:
        return ""
    limpio = normalizar_texto(texto)
    limpio = re.sub(r"[^a-z0-9\s]", " ", limpio)
    return re.sub(r"\s+", " ", limpio).strip()


def distancia_tipeo(a: str, b: str) -> int:
    """Distancia de Damerau-Levenshtein con transposición de dos letras vecinas."""
    la = len(a)
    lb = len(b)
    if la == 0:
        return lb
    if lb == 0:
        return la
    matriz = [[0] * (lb + 1) for _ in range(la + 1)]
    for i in range(la + 1):
        matriz[i][0] = i
    for j in range(lb + 1):
        matriz[0][j] = j
    for i in range(1, la + 1):
        for j in range(1, lb + 1):
            costo = 0 if a[i - 1] == b[j - 1] else 1
            mejor = min(
                matriz[i - 1][j] + 1,
                matriz[i][j - 1] + 1,
                matriz[i - 1][j - 1] + costo,
            )
            if i > 1 and j > 1 and a[i - 1] == b[j - 2] and a[i - 2] == b[j - 1]:
                mejor = min(mejor, matriz[i - 2][j - 2] + 1)
            matriz[i][j] = mejor
    return matriz[la][lb]


def _evaluar_nivel(
    q: str,
    cat_norm: str,
    sub_norm: str | None,
    frases: list[str],
    palabras: list[str],
) -> int | None:
    """Devuelve el mejor nivel (1..5) para una entrada o None si no coincide."""
    if sub_norm is not None:
        if sub_norm == q:
            return 1
        if sub_norm.startswith(q) or cat_norm == q:
            return 2
    if any(f == q for f in frases):
        return 3
    if len(q) >= 3:
        if any(f.startswith(q) for f in frases) or any(p.startswith(q) for p in palabras):
            return 4
    if len(q) >= 5:
        for palabra in palabras:
            if len(palabra) >= 5 and distancia_tipeo(q, palabra) == 1:
                return 5
    return None


def _extraer_frases(texto: str | None) -> list[str]:
    """Divide una descripción separada por comas y normaliza cada frase."""
    if not texto:
        return []
    frases: list[str] = []
    for parte in texto.split(","):
        norm = normalizar_busqueda(parte)
        if norm:
            frases.append(norm)
    return frases


def buscar_en_catalogo(texto: str, tipo: str) -> list[tuple[str, str | None]]:
    """
    Busca en ARBOL_CATEGORIAS por niveles N1..N5 sin acceder a la base.
    Devuelve hasta 8 pares (categoria, subcategoria | None) sin repetidos.
    """
    if tipo not in ("egreso", "ingreso"):
        return []
    q = normalizar_busqueda(texto)
    if len(q) < 2:
        return []

    candidatos: list[tuple[int, int, int, str, str | None]] = []
    categorias_ordenadas = sorted(ARBOL_CATEGORIAS, key=lambda c: c.orden)
    for cat in categorias_ordenadas:
        if cat.tipo != tipo or cat.nombre == "Ahorro":
            continue
        cat_norm = normalizar_busqueda(cat.nombre)
        subs_ordenadas = sorted(cat.subcategorias, key=lambda s: s.orden)
        for sub in subs_ordenadas:
            if sub.es_sistema:
                continue
            sub_norm = normalizar_busqueda(sub.nombre)
            frases = _extraer_frases(sub.descripcion)
            terminos = [sub_norm, cat_norm, *frases]
            palabras = [p for term in terminos for p in term.split()]
            nivel = _evaluar_nivel(q, cat_norm, sub_norm, frases, palabras)
            if nivel is not None:
                candidatos.append((nivel, cat.orden, sub.orden, cat.nombre, sub.nombre))

        if cat.descripcion_otros:
            frases_otros = _extraer_frases(cat.descripcion_otros)
            palabras_otros = [p for term in frases_otros for p in term.split()]
            nivel_otros = _evaluar_nivel(q, cat_norm, None, frases_otros, palabras_otros)
            if nivel_otros is not None:
                candidatos.append((nivel_otros, cat.orden, 999999, cat.nombre, None))

    candidatos.sort(key=lambda item: (item[0], item[1], item[2]))
    resultados: list[tuple[str, str | None]] = []
    vistos: set[tuple[str, str | None]] = set()
    for _, _, _, cat_nombre, sub_nombre in candidatos:
        par = (cat_nombre, sub_nombre)
        if par in vistos:
            continue
        vistos.add(par)
        resultados.append(par)
        if len(resultados) >= MAX_RESULTADOS_CATALOGO:
            break
    return resultados


def buscar_en_memoria(
    db: Session,
    usuario_id: UUID,
    texto: str,
    tipo: str,
    cats_por_id: dict[str, dict[str, Any]],
    subs_por_id: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """
    Busca en memoria_comercios del usuario por inclusión mutua de clave_comercio.
    Devuelve hasta 3 resultados más recientes resueltos contra categorías activas.
    """
    clave_q = clave_comercio(texto)
    if not clave_q or tipo not in ("egreso", "ingreso"):
        return []

    stmt = (
        select(MemoriaComercio)
        .where(
            MemoriaComercio.usuario_id == usuario_id,
            MemoriaComercio.tipo == tipo,
        )
        .order_by(desc(MemoriaComercio.fecha_actualizacion))
    )
    memorias = db.execute(stmt).scalars().all()

    resultados: list[dict[str, Any]] = []
    vistos: set[tuple[UUID, UUID | None]] = set()
    for mem in memorias:
        if not mem.clave or (clave_q not in mem.clave and mem.clave not in clave_q):
            continue
        cat = cats_por_id.get(str(mem.categoria_id))
        if not cat or resolver_alias(cat["nombre"]) == "Ahorro":
            continue
        sub_id: UUID | None = None
        sub_nombre: str | None = None
        if mem.subcategoria_id is not None:
            sub = subs_por_id.get(str(mem.subcategoria_id))
            if not sub or str(sub["categoria_id"]) != str(cat["id"]) or sub.get("es_sistema"):
                continue
            sub_id = sub["id"]
            sub_nombre = sub["nombre"]

        par_id = (cat["id"], sub_id)
        if par_id in vistos:
            continue
        vistos.add(par_id)
        resultados.append(
            {
                "categoria_id": cat["id"],
                "subcategoria_id": sub_id,
                "categoria": cat["nombre"],
                "subcategoria": sub_nombre,
                "origen": "memoria",
            }
        )
        if len(resultados) >= MAX_RESULTADOS_MEMORIA:
            break
    return resultados
