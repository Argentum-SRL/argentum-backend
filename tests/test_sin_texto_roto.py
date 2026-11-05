# -*- coding: utf-8 -*-
"""
Test de integridad textual: verifica que ningún archivo .py de app/services contenga caracteres rotos (mojibake).
Ubicación: tests/test_sin_texto_roto.py
"""
from __future__ import annotations

from pathlib import Path


def test_t13_ningun_py_contiene_texto_roto():
    """T-13. Ningún .py de app\\services contiene 'Ã' ni 'Â' (leer en UTF-8)."""
    base_dir = Path(__file__).resolve().parents[1]
    serv_dir = base_dir / "app" / "services"

    archivos_py = list(serv_dir.glob("*.py"))
    assert len(archivos_py) > 0, "No se encontraron archivos .py en app/services"

    for py_file in archivos_py:
        contenido = py_file.read_text(encoding="utf-8")
        assert "Ã" not in contenido, f"El archivo {py_file.name} contiene 'Ã'"
        assert "Â" not in contenido, f"El archivo {py_file.name} contiene 'Â'"

