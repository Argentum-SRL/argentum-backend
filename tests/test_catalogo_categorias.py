# -*- coding: utf-8 -*-
"""
tests/test_catalogo_categorias.py - Pruebas T.1, T.2 y T.3 para fase_categorias.
"""
from __future__ import annotations

import json
import unicodedata
from pathlib import Path
from unittest.mock import MagicMock

from app.core.catalogo_categorias import (
    ARBOL_CATEGORIAS,
    ALIAS_NOMBRES,
    nombres_categorias_visibles,
    todos_los_nombres_visibles,
    obtener_arbol,
    obtener_subcategoria,
)


def norm_str(s: str) -> str:
    s_norm = unicodedata.normalize("NFD", s.lower())
    return "".join(c for c in s_norm if unicodedata.category(c) != "Mn").strip()


# ==============================================================================
# T.1: El catálogo es exactamente el de la decisión 1
# ==============================================================================

def test_t1_catalogo_decision_1():
    arbol = obtener_arbol()

    # 1. Exactamente 20 categorías activas (15 egreso, 5 ingreso)
    cats_activas = [c for c in arbol]
    assert len(cats_activas) == 20

    egresos = [c for c in arbol if c.tipo == "egreso"]
    ingresos = [c for c in arbol if c.tipo == "ingreso"]
    assert len(egresos) == 15
    assert len(ingresos) == 5

    # 2. Tipos y orden de categorías según Decisión 1
    orden_egreso_esperado = [
        ("Alimentación", 1),
        ("Gastronomía", 2),
        ("Transporte", 3),
        ("Vivienda", 4),
        ("Hogar", 5),
        ("Salud", 6),
        ("Cuidado personal", 7),
        ("Ocio", 8),
        ("Educación", 9),
        ("Indumentaria", 10),
        ("Tecnología", 11),
        ("Impuestos", 12),
        ("Banco", 13),
        ("Otros", 14),
        ("Ahorro", 90),
    ]
    for i, (nom, ord_exp) in enumerate(orden_egreso_esperado):
        assert egresos[i].nombre == nom
        assert egresos[i].orden == ord_exp

    orden_ingreso_esperado = [
        ("Trabajo", 1),
        ("Jubilación y AUH", 2),
        ("Inversiones", 3),
        ("Otros", 4),
        ("Ahorro", 90),
    ]
    for i, (nom, ord_exp) in enumerate(orden_ingreso_esperado):
        assert ingresos[i].nombre == nom
        assert ingresos[i].orden == ord_exp

    # 3. Subcategorías visibles y de sistema
    total_visibles = 0
    total_visibles_egreso = 0
    total_visibles_ingreso = 0
    subcats_sistema = []

    nombres_visibles_egreso = set()
    nombres_visibles_ingreso = set()

    for c in arbol:
        # Como mucho 6 visibles por categoría
        visibles_cat = [s for s in c.subcategorias if not s.es_sistema]
        assert len(visibles_cat) <= 6

        # Nombres de 21 caracteres o menos
        assert len(c.nombre) <= 21, f"Categoría {c.nombre} supera 21 caracteres"

        for s in c.subcategorias:
            assert len(s.nombre) <= 21, f"Subcategoría {s.nombre} supera 21 caracteres"
            if s.es_sistema:
                subcats_sistema.append((c.nombre, s.nombre))
            else:
                total_visibles += 1
                if c.tipo == "egreso":
                    total_visibles_egreso += 1
                    n_s = norm_str(s.nombre)
                    assert n_s not in nombres_visibles_egreso, f"Repetido en egreso: {s.nombre}"
                    nombres_visibles_egreso.add(n_s)
                else:
                    total_visibles_ingreso += 1
                    n_s = norm_str(s.nombre)
                    assert n_s not in nombres_visibles_ingreso, f"Repetido en ingreso: {s.nombre}"
                    nombres_visibles_ingreso.add(n_s)

    assert total_visibles == 68
    assert total_visibles_egreso == 56
    assert total_visibles_ingreso == 12

    # 2 de sistema: Banco > Tarjeta de crédito y Banco > Impuestos
    assert len(subcats_sistema) == 2
    assert ("Banco", "Tarjeta de crédito") in subcats_sistema
    assert ("Banco", "Impuestos") in subcats_sistema

    # Sin nombres repetidos entre categorías y subcategorías visibles dentro del mismo tipo
    nombres_cats_egreso = {norm_str(c.nombre) for c in egresos}
    assert len(nombres_cats_egreso & nombres_visibles_egreso) == 0

    nombres_cats_ingreso = {norm_str(c.nombre) for c in ingresos}
    assert len(nombres_cats_ingreso & nombres_visibles_ingreso) == 0

    # Divisiones IPC
    valid_ipc = {f"{i:02d}" for i in range(1, 13)} | {"general", "fuera"}
    for c in arbol:
        for s in c.subcategorias:
            if c.tipo == "egreso":
                assert s.division_ipc in valid_ipc, f"IPC inválido para {c.nombre} > {s.nombre}: {s.division_ipc}"
            else:
                assert s.division_ipc is None, f"Subcategoría de ingreso con división IPC: {s.nombre}"

    # Verificaciones explícitas de subcategorías y división IPC
    assert obtener_subcategoria("Ocio", "Deportes y gimnasio", "egreso").division_ipc == "09"
    assert obtener_subcategoria("Cuidado personal", "Peluquería y belleza", "egreso").division_ipc == "12"
    assert obtener_subcategoria("Hogar", "Servicio doméstico", "egreso").division_ipc == "05"
    assert obtener_subcategoria("Otros", "Pagos a personas", "egreso").division_ipc == "general"
    assert obtener_subcategoria("Otros", "Ayuda familiar", "egreso").division_ipc == "fuera"
    assert obtener_subcategoria("Alimentación", "Supermercado", "egreso").division_ipc == "01"

    # El árbol escrito en la migración es igual al del catálogo
    import importlib.util
    migr_path = Path(__file__).resolve().parent.parent / "alembic" / "versions" / "a5f6a7b8c9d0_categorias_v2.py"
    spec = importlib.util.spec_from_file_location("a5f6a7b8c9d0_categorias_v2", migr_path)
    migr_mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migr_mod)
    FOTO_ARBOL_DECISION_1 = migr_mod.FOTO_ARBOL_DECISION_1
    assert len(FOTO_ARBOL_DECISION_1) == len(arbol)
    for i, cat_migr in enumerate(FOTO_ARBOL_DECISION_1):
        cat_cat = arbol[i]
        assert cat_migr[0] == cat_cat.nombre
        assert cat_migr[1] == cat_cat.tipo
        assert cat_migr[2] == cat_cat.orden
        assert cat_migr[3] == cat_cat.icono
        assert cat_migr[4] == cat_cat.color
        assert len(cat_migr[5]) == len(cat_cat.subcategorias)
        for j, sub_migr in enumerate(cat_migr[5]):
            sub_cat = cat_cat.subcategorias[j]
            assert sub_migr[0] == sub_cat.nombre
            assert sub_migr[1] == sub_cat.orden
            assert sub_migr[2] == sub_cat.es_sistema
            assert sub_migr[3] == sub_cat.division_ipc


# ==============================================================================
# T.2: Nombres en el código
# ==============================================================================

def test_t2_nombres_en_el_codigo():
    visibles_cat_sub = todos_los_nombres_visibles()
    visibles_cats = nombres_categorias_visibles()

    # 1. SYSTEM_PROMPT y Decisión 18 (descripciones y bloque IA)
    from unittest.mock import patch
    from app.services.ai_service import SYSTEM_PROMPT, _construir_system_prompt_completo, invalidar_cache_schema
    from app.services import categoria_service

    # a. SYSTEM_PROMPT no contiene "MAPEO DE JERGA"
    assert "MAPEO DE JERGA" not in SYSTEM_PROMPT

    # b. Cada subcategoría visible tiene descripción no vacía <= 160 caracteres
    for cat in ARBOL_CATEGORIAS:
        for sub in cat.subcategorias:
            if not sub.es_sistema:
                assert sub.descripcion, f"Subcategoría visible {cat.nombre} > {sub.nombre} sin descripción"
                assert len(sub.descripcion) <= 160, f"Descripción de {cat.nombre} > {sub.nombre} supera 160 caracteres ({len(sub.descripcion)})"

    # c. Bloque de categorías de IA tiene <subcategoría>: <descripción> y excluye sistema/Ahorro
    invalidar_cache_schema()
    db_mock = MagicMock()
    cats_mock = [
        {"id": str(i), "nombre": c.nombre, "tipo": c.tipo, "orden": c.orden}
        for i, c in enumerate(ARBOL_CATEGORIAS)
    ]
    subs_mock = [
        {
            "id": f"{i}_{j}",
            "categoria_id": str(i),
            "nombre": s.nombre,
            "orden": s.orden,
            "es_sistema": s.es_sistema,
        }
        for i, c in enumerate(ARBOL_CATEGORIAS)
        for j, s in enumerate(c.subcategorias)
    ]
    with patch.object(categoria_service, "obtener_categorias_globales", return_value=(cats_mock, subs_mock)):
        prompt_completo = _construir_system_prompt_completo(db_mock)

    assert "- Ahorro" not in prompt_completo
    assert "  - Tarjeta de crédito" not in prompt_completo
    assert "- Alimentación (egreso). Otros de esta categoría: fiambrería, dietética, vinoteca" in prompt_completo

    for cat in ARBOL_CATEGORIAS:
        if cat.nombre == "Ahorro":
            continue
        for sub in cat.subcategorias:
            if not sub.es_sistema:
                assert f"  - {sub.nombre}: {sub.descripcion}" in prompt_completo

    # d. Tamaño total de texto <= 10% de crecimiento vs a8e1322
    # Largo del SYSTEM_PROMPT en a8e1322, medido por script (crudo _12 de fase_categorias).
    # Queda fijo: la CI clona sin historia y no puede leer a8e1322.
    longitud_base = 27422
    assert len(prompt_completo) <= longitud_base * 1.10, (
        f"Crecimiento supera el 10%: actual={len(prompt_completo)}, base={longitud_base}, max={longitud_base * 1.10}"
    )

    # 2. Todo par del catálogo de suscripciones (servicios y reglas)
    cat_susc_path = Path(__file__).resolve().parent.parent / "app" / "core" / "catalogo_suscripciones.json"
    with open(cat_susc_path, "r", encoding="utf-8") as f:
        data_susc = json.load(f)

    for serv in data_susc.get("servicios", []):
        cat_nom = serv["categoria"]
        sub_nom = serv.get("subcategoria")
        assert cat_nom in visibles_cats, f"Categoría de suscripción desconocida: {cat_nom}"
        if sub_nom:
            assert obtener_subcategoria(cat_nom, sub_nom, "egreso") is not None, f"Subcategoría desconocida en catálogo: {cat_nom} > {sub_nom}"

    for regla in data_susc.get("reglas_categoria", []):
        cat_nom = regla["categoria"]
        sub_nom = regla.get("subcategoria")
        assert cat_nom in visibles_cats, f"Categoría de regla desconocida: {cat_nom}"
        if sub_nom:
            assert obtener_subcategoria(cat_nom, sub_nom, "egreso") is not None, f"Subcategoría desconocida en regla: {cat_nom} > {sub_nom}"

    # 3. Todo destino de vocabulario_categorias existe como nombre visible
    from app.core.vocabulario_categorias import PADRES_DESTINO
    for destino in PADRES_DESTINO.keys():
        assert destino in visibles_cat_sub, f"Destino de vocabulario no visible: {destino}"

    # 4. Todo nombre de SUBCATEGORIAS_COMPROMISO_ELEGIBLES y CATEGORIAS_COMPROMISO_DIRECTAS es nombre del catálogo o clave de ALIAS_NOMBRES
    from app.utils.finanzas import SUBCATEGORIAS_COMPROMISO_ELEGIBLES, CATEGORIAS_COMPROMISO_DIRECTAS
    todos_nombres_catalogo = [c.nombre for c in ARBOL_CATEGORIAS] + [s.nombre for c in ARBOL_CATEGORIAS for s in c.subcategorias]
    todos_catalogo_norm = {norm_str(n) for n in todos_nombres_catalogo}
    alias_norm = {norm_str(k): v for k, v in ALIAS_NOMBRES.items()}

    for sub_comp in SUBCATEGORIAS_COMPROMISO_ELEGIBLES:
        sn = norm_str(sub_comp)
        valido = sn in todos_catalogo_norm or sn in alias_norm
        assert valido, f"Subcategoría de compromiso no válida: {sub_comp}"

    for cat_comp in CATEGORIAS_COMPROMISO_DIRECTAS:
        cn = norm_str(cat_comp)
        valido = cn in todos_catalogo_norm or cn in alias_norm
        assert valido, f"Categoría de compromiso no válida: {cat_comp}"

    # 5. Todo destino de ALIAS_NOMBRES existe en el catálogo
    for k, dest in ALIAS_NOMBRES.items():
        assert dest in visibles_cat_sub, f"Destino de ALIAS_NOMBRES no existe: {k} -> {dest}"


# ==============================================================================
# T.3: Resolver de WhatsApp con el árbol nuevo cargado
# ==============================================================================

def test_t3_resolver_whatsapp_arbol_nuevo():
    from uuid import uuid4
    from app.models.categoria import TipoCategoria, EstadoCategoria
    from app.models.subcategoria import EstadoSubcategoria
    from app.routers.whatsapp.db_lookups import _resolver_categoria_y_subcategoria

    class FakeCat:
        def __init__(self, cid, nombre, tipo):
            self.id = cid
            self.nombre = nombre
            self.tipo = TipoCategoria.INGRESO if tipo == "ingreso" else TipoCategoria.EGRESO
            self.estado = EstadoCategoria.ACTIVA

    class FakeSub:
        def __init__(self, sid, cid, nombre, es_sistema=False):
            self.id = sid
            self.categoria_id = cid
            self.nombre = nombre
            self.es_sistema = es_sistema
            self.orden = 0
            self.estado = EstadoSubcategoria.ACTIVA

    cats_db = []
    subs_db = []

    for c in ARBOL_CATEGORIAS:
        cat_id = uuid4()
        cat_obj = FakeCat(cat_id, c.nombre, c.tipo)
        cats_db.append(cat_obj)
        for s in c.subcategorias:
            sub_id = uuid4()
            sub_obj = FakeSub(sub_id, cat_id, s.nombre, s.es_sistema)
            subs_db.append(sub_obj)

    class FakeScalarResult:
        def __init__(self, data):
            self._data = data
        def all(self):
            return self._data
        def first(self):
            return self._data[0] if self._data else None

    class FakeExecResult:
        def __init__(self, data):
            self._data = data
        def scalars(self):
            return FakeScalarResult(self._data)
        def all(self):
            return self._data
        def first(self):
            return self._data[0] if self._data else None

    db_mock = MagicMock()
    last_tipo = [TipoCategoria.EGRESO]

    def fake_execute(stmt, *args, **kwargs):
        stmt_str = str(stmt).lower()
        if "from subcategorias" in stmt_str:
            valid_cids = {c.id for c in cats_db if c.tipo == last_tipo[0]}
            return FakeExecResult([s for s in subs_db if not s.es_sistema and s.categoria_id in valid_cids])
        elif "from categorias" in stmt_str:
            try:
                compiled = stmt.compile()
                params = list(compiled.params.values()) if compiled and compiled.params else []
            except Exception:
                params = []
            if TipoCategoria.INGRESO in params or "ingreso" in params or "ingreso" in stmt_str:
                last_tipo[0] = TipoCategoria.INGRESO
                return FakeExecResult([c for c in cats_db if c.tipo == TipoCategoria.INGRESO])
            last_tipo[0] = TipoCategoria.EGRESO
            return FakeExecResult([c for c in cats_db if c.tipo == TipoCategoria.EGRESO])
        return FakeExecResult([])

    db_mock.execute.side_effect = fake_execute

    user_id = uuid4()

    def resolver(nombre: str, tipo: str = "egreso"):
        cid, sid = _resolver_categoria_y_subcategoria(nombre, user_id, db_mock, tipo=tipo)
        cat_nom = next((c.nombre for c in cats_db if c.id == cid), None)
        sub_nom = next((s.nombre for s in subs_db if s.id == sid), None) if sid else None
        return cat_nom, sub_nom

    # Casos de prueba exactos de T.3
    assert resolver("Panadería", "egreso") == ("Alimentación", "Panadería")
    assert resolver("Impuestos", "egreso") == ("Impuestos", None)
    assert resolver("Alimentación", "egreso") == ("Alimentación", None)
    assert resolver("Taxi / Apps", "egreso") == ("Transporte", "Taxi y apps")
    assert resolver("Obra social / Prepaga", "egreso") == ("Salud", "Prepaga y obra social")
    assert resolver("Cuotas", "egreso") == ("Educación", "Colegio y facultad")
    assert resolver("Recreativo", "egreso") == ("Ocio", None)
    assert resolver("Trabajo independiente", "ingreso") == ("Trabajo", None)
    assert resolver("Intereses", "egreso") == ("Banco", "Intereses")
    assert resolver("Intereses", "ingreso") == ("Inversiones", "Intereses")
    assert resolver("Regalos", "ingreso") == ("Otros", "Regalos")
    assert resolver("Tarjeta de crédito", "egreso") == ("Otros", None)
    assert resolver("coso raro", "egreso") == ("Otros", None)
