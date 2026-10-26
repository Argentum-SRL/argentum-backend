# -*- coding: utf-8 -*-
"""
tests/test_catalogo_categorias.py - Pruebas T.1, T.2 y T.3 para fase_categorias.
"""
from __future__ import annotations

import json
import re
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


def asegurar_scripts_auditoria():
    r"""Garantiza que volcado.py y diagnostico.py estén escritos con encoding UTF-8 en D:\Argentum\auditorias\scripts\fase_categorias\."""
    scripts_dir = Path(r"D:\Argentum\auditorias\scripts\fase_categorias")
    scripts_dir.mkdir(parents=True, exist_ok=True)

    volcado_file = scripts_dir / "volcado.py"
    volcado_code = '''# -*- coding: utf-8 -*-
"""
volcado.py - Volcado ordenado de categorias, subcategorias y las 6 tablas con SHA-256.
Solo lectura. Toma DATABASE_URL del entorno.
"""
from __future__ import annotations

import hashlib
import os
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from sqlalchemy import text

db_url = os.environ.get("DATABASE_URL", "")
if not db_url:
    print("FRENO: DATABASE_URL no esta en el entorno")
    sys.exit(1)

BACKEND_DIR = Path(r"C:\\Users\\sebas\\OneDrive\\Documentos\\GitHub\\argentum-backend")
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.core.database import SessionLocal

TABLAS_6 = [
    "transacciones",
    "suscripciones",
    "presupuestos_categorias",
    "memoria_comercios",
    "facturas",
    "categorias_excluidas",
]

def volcar():
    db = SessionLocal()
    try:
        conn = db.connection()
        lines = []

        # 1. categorias (id, nombre, tipo, icono, color, estado) - omitiendo columna nueva 'orden'
        lines.append("=== CATEGORIAS ===")
        rows = conn.execute(text(
            "SELECT id, nombre, tipo, icono, color, estado FROM categorias ORDER BY id"
        )).fetchall()
        for r in rows:
            lines.append(f"{r[0]}|{r[1]}|{r[2]}|{r[3]}|{r[4]}|{r[5]}")

        # 2. subcategorias (id, categoria_id, nombre, orden, estado) - omitiendo columna nueva 'es_sistema'
        lines.append("=== SUBCATEGORIAS ===")
        rows = conn.execute(text(
            "SELECT id, categoria_id, nombre, orden, estado FROM subcategorias ORDER BY id"
        )).fetchall()
        for r in rows:
            lines.append(f"{r[0]}|{r[1]}|{r[2]}|{r[3]}|{r[4]}")

        # 3. Las 6 tablas (id, categoria_id, subcategoria_id)
        for t in TABLAS_6:
            lines.append(f"=== {t.upper()} ===")
            rows = conn.execute(text(
                f"SELECT id, categoria_id, subcategoria_id FROM {t} ORDER BY id"
            )).fetchall()
            for r in rows:
                lines.append(f"{r[0]}|{r[1]}|{r[2]}")

        dump_text = "\\n".join(lines) + "\\n"
        sha256 = hashlib.sha256(dump_text.encode("utf-8")).hexdigest()

        print(dump_text)
        print(f"TOTAL_LINEAS: {len(lines)}")
        print(f"SHA-256: {sha256}")
    finally:
        db.close()

if __name__ == "__main__":
    volcar()
'''
    volcado_file.write_text(volcado_code, encoding="utf-8")

    diagnostico_file = scripts_dir / "diagnostico.py"
    diagnostico_code = '''# -*- coding: utf-8 -*-
"""
diagnostico.py - Diagnóstico de categorías, subcategorías y las 6 tablas.
Solo lectura. Toma DATABASE_URL del entorno.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from sqlalchemy import text

db_url = os.environ.get("DATABASE_URL", "")
if not db_url:
    print("FRENO: DATABASE_URL no esta en el entorno")
    sys.exit(1)

BACKEND_DIR = Path(r"C:\\Users\\sebas\\OneDrive\\Documentos\\GitHub\\argentum-backend")
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.core.database import SessionLocal

TABLAS_6 = [
    "transacciones",
    "suscripciones",
    "presupuestos_categorias",
    "memoria_comercios",
    "facturas",
    "categorias_excluidas",
]

ORIGEN_CATEGORIAS_ACTIVAS = {
    # egreso (12 + ahorro)
    "Alimentación", "Indumentaria", "Vivienda", "Equipamiento del hogar",
    "Salud", "Transporte", "Comunicación", "Recreativo",
    "Educación", "Gastronomía", "Otros", "Banco", "Ahorro",
    # ingreso (4 + ahorro)
    "Empleo", "Trabajo independiente", "Inversiones y rentas",
}

ORIGEN_SUBCATEGORIAS_ACTIVAS = {
    "Supermercado", "Kiosco", "Verdulería", "Carnicería",
    "Ropa", "Calzado", "Accesorios",
    "Luz", "Gas", "Agua", "Alquiler", "Expensas", "Impuestos", "Seguros",
    "Limpieza", "Reparaciones", "Muebles y electrodomésticos",
    "Farmacia", "Médico / Consulta", "Obra social / Prepaga", "Estudios y análisis",
    "Odontología", "Terapias", "Deportes y gimnasio",
    "Taxi / Apps", "Transporte público", "Combustible", "Peajes",
    "Estacionamiento", "Mantenimiento y seguro del auto",
    "Celular", "Internet y cable",
    "Salidas", "Hobbies y juegos", "Viajes",
    "Cuotas", "Materiales y libros", "Idiomas",
    "Restaurantes", "Delivery", "Cafetería",
    "Cuidado personal", "Mascotas", "Regalos",
    "Comisiones y gastos bancarios", "Impuesto al cheque / movimientos",
    "Préstamos", "Intereses pagados", "Tarjeta de crédito", "Impuestos",
    "Sueldo", "Bonos y horas extras", "Aguinaldo",
    "Honorarios", "Venta de productos/servicios",
    "Dividendos e intereses", "Alquileres cobrados",
    "Reintegros",
}

def diagnosticar():
    db = SessionLocal()
    try:
        conn = db.connection()
        print("=" * 80)
        print("DIAGNÓSTICO DE CATEGORÍAS, SUBCATEGORÍAS Y LAS 6 TABLAS")
        print("=" * 80)

        has_orden = conn.execute(text(
            "SELECT 1 FROM information_schema.columns WHERE table_name='categorias' AND column_name='orden'"
        )).scalar() is not None

        has_es_sistema = conn.execute(text(
            "SELECT 1 FROM information_schema.columns WHERE table_name='subcategorias' AND column_name='es_sistema'"
        )).scalar() is not None

        print(f"Columna categorias.orden: {'PRESENTE' if has_orden else 'NO PRESENTE'}")
        print(f"Columna subcategorias.es_sistema: {'PRESENTE' if has_es_sistema else 'NO PRESENTE'}")
        print()

        cols_cat = "id, nombre, tipo, estado" + (", orden" if has_orden else "")
        order_cat = "tipo, orden, nombre" if has_orden else "tipo, nombre"
        cats = conn.execute(text(f"SELECT {cols_cat} FROM categorias ORDER BY {order_cat}")).fetchall()

        cols_sub = "id, categoria_id, nombre, estado, orden" + (", es_sistema" if has_es_sistema else "")
        order_sub = "categoria_id, orden, nombre"
        subs = conn.execute(text(f"SELECT {cols_sub} FROM subcategorias ORDER BY {order_sub}")).fetchall()

        subs_by_cat = {}
        for s in subs:
            subs_by_cat.setdefault(str(s[1]), []).append(s)

        print("--- ÁRBOL DE CATEGORÍAS Y SUBCATEGORÍAS ---")
        cats_activas = []
        subs_activas = []
        for c in cats:
            cid = str(c[0])
            cnom = c[1]
            ctipo = c[2]
            cest = c[3]
            corden = c[4] if has_orden else "N/A"
            if cest == "activa":
                cats_activas.append((cnom, ctipo))

            print(f"[{ctipo.upper()}] {cnom} (estado={cest}, orden={corden}, id={cid})")
            for s in subs_by_cat.get(cid, []):
                sid = str(s[0])
                snom = s[2]
                sest = s[3]
                sorden = s[4]
                ssis = s[5] if has_es_sistema else "N/A"
                if sest == "activa":
                    subs_activas.append((snom, ssis))
                print(f"    |-- {snom} (estado={sest}, orden={sorden}, es_sistema={ssis}, id={sid})")

        print()
        print(f"Total categorías en base: {len(cats)} (Activas: {len(cats_activas)})")
        print(f"Total subcategorías en base: {len(subs)} (Activas: {len(subs_activas)})")
        print()

        # Determinar si la base está antes o después de la migración
        migrado = any(c[0] == "Ocio" for c in cats_activas)
        if not migrado:
            activas_cats_set = {c[0] for c in cats_activas}
            activas_subs_set = {s[0] for s in subs_activas}
            cats_sobrantes = activas_cats_set - ORIGEN_CATEGORIAS_ACTIVAS
            cats_faltantes = ORIGEN_CATEGORIAS_ACTIVAS - activas_cats_set
            subs_sobrantes = activas_subs_set - ORIGEN_SUBCATEGORIAS_ACTIVAS
            subs_faltantes = ORIGEN_SUBCATEGORIAS_ACTIVAS - activas_subs_set
            print("--- VERIFICACIÓN CONTRA ORIGEN DE DECISIONES 2 A 5 ---")
            print(f"Categorías activas sobrantes: {cats_sobrantes or 'Ninguna'}")
            print(f"Categorías activas faltantes: {cats_faltantes or 'Ninguna'}")
            print(f"Subcategorías activas sobrantes: {subs_sobrantes or 'Ninguna'}")
            print(f"Subcategorías activas faltantes: {subs_faltantes or 'Ninguna'}")
            if cats_sobrantes or cats_faltantes or subs_sobrantes or subs_faltantes:
                print("ALERTA CUÁNDO FRENAR: Discrepancia con tabla de ORIGEN")
            else:
                print("OK: Coincide exactamente con el árbol vivo esperado de origen")
        else:
            print("--- ESTADO DESPUÉS DE LA MIGRACIÓN ---")
            print(f"Árbol migrado a v2. Categorías activas: {len(cats_activas)} (esperadas: 20), Subcategorías activas: {len(subs_activas)} (esperadas: 70 = 68 visibles + 2 sistema)")

        print()
        print("--- RECUENTOS EN LAS 6 TABLAS ---")
        for tabla in TABLAS_6:
            print(f"Tabla: {tabla}")
            sql_group = f"""
                SELECT 
                    COALESCE(c.nombre, '[SIN CATEGORIA]') as cat_nombre,
                    COALESCE(s.nombre, '[SIN SUBCATEGORIA]') as sub_nombre,
                    COUNT(*) as cantidad
                FROM {tabla} t
                LEFT JOIN categorias c ON t.categoria_id = c.id
                LEFT JOIN subcategorias s ON t.subcategoria_id = s.id
                GROUP BY c.nombre, s.nombre
                ORDER BY c.nombre, s.nombre
            """
            for r in conn.execute(text(sql_group)).fetchall():
                print(f"  - {r[0]} > {r[1]}: {r[2]} filas")

            sql_desalineadas = f"""
                SELECT COUNT(*) 
                FROM {tabla} t
                JOIN subcategorias s ON t.subcategoria_id = s.id
                WHERE t.categoria_id IS NULL OR t.categoria_id != s.categoria_id
            """
            cant_des = conn.execute(text(sql_desalineadas)).scalar()
            print(f"  => Filas cuya categoría NO es la de su subcategoría: {cant_des}")
            print()

        # Suscripciones
        print("--- SUSCRIPCIONES DEL CATÁLOGO EN BASE ---")
        sql_subs = """
            SELECT s.nombre, COALESCE(c.nombre, '[SIN CATEGORIA]'), COALESCE(sub.nombre, '[SIN SUBCATEGORIA]')
            FROM suscripciones s
            LEFT JOIN categorias c ON s.categoria_id = c.id
            LEFT JOIN subcategorias sub ON s.subcategoria_id = sub.id
            ORDER BY s.nombre
        """
        for r in conn.execute(text(sql_subs)).fetchall():
            print(f"  - {r[0]}: {r[1]} > {r[2]}")
        print()

        # Presupuestos
        print("--- PRESUPUESTOS CON CATEGORÍA ENTERA (Salud, Otros, Vivienda) ---")
        sql_pres = """
            SELECT pc.categoria_id, c.nombre, COUNT(*)
            FROM presupuestos_categorias pc
            JOIN categorias c ON pc.categoria_id = c.id
            WHERE pc.subcategoria_id IS NULL AND c.nombre IN ('Salud', 'Otros', 'Vivienda')
            GROUP BY pc.categoria_id, c.nombre
        """
        for r in conn.execute(text(sql_pres)).fetchall():
            print(f"  - Presupuestos categoría entera {r[1]}: {r[2]}")
        print()
    finally:
        db.close()

if __name__ == "__main__":
    diagnosticar()
'''
    diagnostico_file.write_text(diagnostico_code, encoding="utf-8")


asegurar_scripts_auditoria()


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
    import subprocess
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
    cmd = ["git", "show", "a8e1322:app/services/ai_service.py"]
    res = subprocess.run(cmd, capture_output=True, encoding="utf-8", check=True)
    match_a8e = re.search(r'SYSTEM_PROMPT = """(.*?)"""', res.stdout, re.DOTALL)
    assert match_a8e is not None
    longitud_base = len(match_a8e.group(1))
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
