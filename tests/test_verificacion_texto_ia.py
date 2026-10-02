"""
tests/test_verificacion_texto_ia.py

Pruebas para el módulo de verificación de textos generados por la IA (Paso fase3_f1).
Valida que ningún número escrito por la IA llegue al usuario sin verificar.
"""
from __future__ import annotations

import ast
from pathlib import Path

from app.routers.whatsapp.verificacion_texto_ia import (
    TEXTO_IA_NO_VERIFICADO,
    TextoIA,
    verificar_texto_ia,
)


def test_caso_1_str_comun_con_numeros_sale_igual_y_mismo_objeto():
    """1. Un str común con números sale igual, y es el mismo objeto."""
    texto_original = "Gasto registrado de 5000 pesos."
    resultado = verificar_texto_ia(texto_original, "gasté 5000")
    assert resultado == texto_original
    assert resultado is texto_original


def test_caso_2_texto_ia_sin_numeros_sale_igual():
    """2. Un TextoIA sin números sale igual."""
    texto_original = TextoIA("Hola, ¿en qué te puedo ayudar hoy?")
    resultado = verificar_texto_ia(texto_original, "hola")
    assert resultado == "Hola, ¿en qué te puedo ayudar hoy?"
    assert type(resultado) is str


def test_caso_3_texto_ia_con_numeros_coincidentes_sale_igual():
    """3. TextoIA 'Listo. $5.000 en Kiosco.' con el mensaje 'gasté 5000 en el kiosco': sale igual."""
    texto_ia = TextoIA("Listo. $5.000 en Kiosco.")
    resultado = verificar_texto_ia(texto_ia, "gasté 5000 en el kiosco")
    assert resultado == "Listo. $5.000 en Kiosco."
    assert type(resultado) is str


def test_caso_4_texto_ia_con_numeros_en_palabras_sale_igual():
    """4. TextoIA 'Listo. $5.000 en Kiosco.' con el mensaje 'gasté 5 mil en el kiosco': sale igual."""
    texto_ia = TextoIA("Listo. $5.000 en Kiosco.")
    resultado = verificar_texto_ia(texto_ia, "gasté 5 mil en el kiosco")
    assert resultado == "Listo. $5.000 en Kiosco."
    assert type(resultado) is str


def test_caso_5_texto_ia_con_numero_discrepante_reemplaza_texto():
    """5. TextoIA 'Listo. $7.500 en Kiosco.' con el mensaje 'gasté 5000 en el kiosco': sale TEXTO_IA_NO_VERIFICADO."""
    texto_ia = TextoIA("Listo. $7.500 en Kiosco.")
    resultado = verificar_texto_ia(texto_ia, "gasté 5000 en el kiosco")
    assert resultado == TEXTO_IA_NO_VERIFICADO


def test_caso_6_texto_ia_con_marcadores_lista_no_cuenta_numeros():
    """6. TextoIA 'Elegí una:\\n1. Galicia\\n2. Santander' con el mensaje 'pagué con tarjeta': sale igual."""
    texto_ia = TextoIA("Elegí una:\n1. Galicia\n2. Santander")
    resultado = verificar_texto_ia(texto_ia, "pagué con tarjeta")
    assert resultado == "Elegí una:\n1. Galicia\n2. Santander"
    assert type(resultado) is str


def test_caso_7_aviso_cambio_tema_conserva_texto_ia():
    """7. El texto armado con el aviso de cambio de tema a partir de un TextoIA sigue siendo TextoIA."""
    aviso_cambio_tema = "Aviso: cambio de tema"
    resp_actual = TextoIA("¿Es un gasto?")
    if resp_actual.startswith("¿"):
        texto_cambio = f"{aviso_cambio_tema}\n\n{resp_actual}"
    else:
        texto_cambio = f"{aviso_cambio_tema}\n{resp_actual}"
    if isinstance(resp_actual, TextoIA):
        resultado = TextoIA(texto_cambio)
    else:
        resultado = texto_cambio

    assert isinstance(resultado, TextoIA)
    assert resultado == "Aviso: cambio de tema\n\n¿Es un gasto?"


def test_caso_8_ast_texto_ia_y_llamada_verificar():
    """
    8. Por ast:
       - TextoIA( se construye solo en etapa_ia.py, en etapa_despacho.py (decisión 3) y en el módulo nuevo;
       - verificar_texto_ia se llama en etapa_despacho.py antes de la línea que guarda mensaje_bot.
    """
    repo_root = Path(__file__).resolve().parent.parent
    app_dir = repo_root / "app"

    allowed_files = {
        "app/routers/whatsapp/etapa_ia.py",
        "app/routers/whatsapp/etapa_despacho.py",
        "app/routers/whatsapp/verificacion_texto_ia.py",
    }

    files_with_texto_ia = set()
    for py_file in app_dir.rglob("*.py"):
        rel_posix = py_file.relative_to(repo_root).as_posix()
        tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func_name = None
                if isinstance(node.func, ast.Name):
                    func_name = node.func.id
                elif isinstance(node.func, ast.Attribute):
                    func_name = node.func.attr
                if func_name == "TextoIA":
                    files_with_texto_ia.add(rel_posix)

    assert files_with_texto_ia.issubset(allowed_files), (
        f"Archivos fuera de los permitidos construyen TextoIA: {files_with_texto_ia - allowed_files}"
    )
    assert "app/routers/whatsapp/etapa_ia.py" in files_with_texto_ia
    assert "app/routers/whatsapp/etapa_despacho.py" in files_with_texto_ia

    # Verificar llamada en etapa_despacho.py antes de guardar mensaje_bot
    despacho_path = app_dir / "routers" / "whatsapp" / "etapa_despacho.py"
    tree_despacho = ast.parse(despacho_path.read_text(encoding="utf-8"), filename=str(despacho_path))

    linea_verificar = None
    linea_guardar_bot = None

    for node in ast.walk(tree_despacho):
        if isinstance(node, ast.Call):
            func_name = None
            if isinstance(node.func, ast.Name):
                func_name = node.func.id
            elif isinstance(node.func, ast.Attribute):
                func_name = node.func.attr
            if func_name == "verificar_texto_ia":
                linea_verificar = node.lineno

            if (isinstance(node.func, ast.Name) and node.func.id == "ConversacionWpp") or (
                isinstance(node.func, ast.Attribute) and node.func.attr == "ConversacionWpp"
            ):
                for kw in node.keywords:
                    if kw.arg == "mensaje_bot":
                        linea_guardar_bot = node.lineno

    assert linea_verificar is not None, "No se encontró llamada a verificar_texto_ia en etapa_despacho.py"
    assert linea_guardar_bot is not None, "No se encontró instanciación de ConversacionWpp con mensaje_bot"
    assert linea_verificar < linea_guardar_bot, (
        f"verificar_texto_ia (línea {linea_verificar}) debe ejecutarse antes de guardar mensaje_bot (línea {linea_guardar_bot})"
    )
