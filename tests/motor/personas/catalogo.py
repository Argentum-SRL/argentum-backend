# Acceso a catálogo de categorías e inflación desde fixtures fijas
from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from typing import Any

from tests.motor.personas.modelos import ItemCatalogo


_DIR_ACTUAL = Path(__file__).parent
_RUTA_IPC = _DIR_ACTUAL / "ipc_fixture.json"
_RUTA_CATEGORIAS = _DIR_ACTUAL / "categorias_fixture.json"


def cargar_ipc_fixture() -> dict[str, Decimal]:
    """Carga la serie de IPC oficial indexada por mes (YYYY-MM)."""
    with open(_RUTA_IPC, "r", encoding="utf-8") as f:
        data = json.load(f)
    return {item["fecha_dato"]: Decimal(str(item["indice_acumulado"])) for item in data}


class CatalogoCategorias:
    """Provee acceso en memoria a las categorías y subcategorías oficiales."""

    def __init__(self):
        with open(_RUTA_CATEGORIAS, "r", encoding="utf-8") as f:
            self._data = json.load(f)

        # Índices por nombre normalizado
        self._cat_por_nombre: dict[str, ItemCatalogo] = {}
        self._subcat_por_nombre: dict[tuple[str, str], ItemCatalogo] = {}
        self._subcat_directa: dict[str, ItemCatalogo] = {}
        self._cat_por_subcat: dict[str, ItemCatalogo] = {}

        for cat in self._data:
            c_item = ItemCatalogo(cat["id"], cat["nombre"])
            c_norm = cat["nombre"].strip().casefold()
            self._cat_por_nombre[c_norm] = c_item

            for sub in cat.get("subcategorias", []):
                s_item = ItemCatalogo(sub["id"], sub["nombre"])
                s_norm = sub["nombre"].strip().casefold()
                self._subcat_por_nombre[(c_norm, s_norm)] = s_item
                self._subcat_directa[s_norm] = s_item
                self._cat_por_subcat[s_norm] = c_item

    def categoria(self, nombre: str) -> ItemCatalogo:
        norm = nombre.strip().casefold()
        if norm in self._cat_por_nombre:
            return self._cat_por_nombre[norm]
        raise KeyError(f"Categoría no encontrada en fixture: {nombre}")

    def subcategoria(self, nombre_sub: str, nombre_cat: str | None = None) -> ItemCatalogo:
        s_norm = nombre_sub.strip().casefold()
        if nombre_cat:
            c_norm = nombre_cat.strip().casefold()
            if (c_norm, s_norm) in self._subcat_por_nombre:
                return self._subcat_por_nombre[(c_norm, s_norm)]
        if s_norm in self._subcat_directa:
            return self._subcat_directa[s_norm]
        raise KeyError(f"Subcategoría no encontrada en fixture: {nombre_sub} (cat: {nombre_cat})")

    def categoria_de_subcategoria(self, nombre_sub: str) -> ItemCatalogo:
        s_norm = nombre_sub.strip().casefold()
        if s_norm in self._cat_por_subcat:
            return self._cat_por_subcat[s_norm]
        raise KeyError(f"Categoría para subcategoría no encontrada: {nombre_sub}")


# Instancia única reutilizable
CATALOGO = CatalogoCategorias()
IPC_MAP = cargar_ipc_fixture()
