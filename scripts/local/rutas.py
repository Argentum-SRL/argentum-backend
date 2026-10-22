"""
Módulo centralizado de resolución de rutas y configuración de auditorías de Argentum.
Ubicación: scripts/local/rutas.py

Permite desacoplar las rutas del almacenamiento físico en disco y controlar
parámetros como el límite de copias de respaldo a conservar.
"""
from __future__ import annotations

import os
from pathlib import Path


def auditorias_dir() -> Path:
    """
    Retorna la ruta raíz del directorio de auditorías.
    Lee la variable de entorno ARGENTUM_AUDITORIAS_DIR; si no está definida o está vacía,
    utiliza por defecto D:\\Argentum\\auditorias.
    No crea carpetas en disco.
    """
    val = os.environ.get("ARGENTUM_AUDITORIAS_DIR")
    if val and val.strip():
        return Path(val.strip())
    return Path(r"D:\Argentum\auditorias")


def raw_dir(paso: str | None = None) -> Path:
    """
    Retorna la ruta a la carpeta de crudos (raw) de auditorías.
    Si se proporciona el nombre de un paso, retorna la subcarpeta correspondiente.
    No crea carpetas en disco.
    """
    base = auditorias_dir() / "raw"
    return base / paso if paso else base


def backups_dir(paso: str | None = None) -> Path:
    """
    Retorna la ruta a la carpeta de respaldos (backups) de auditorías.
    Si se proporciona el nombre de un paso, retorna la subcarpeta correspondiente.
    No crea carpetas en disco.
    """
    base = auditorias_dir() / "backups"
    return base / paso if paso else base


def fotos_dir() -> Path:
    """
    Retorna la ruta a la carpeta de fotografías del motor financiero.
    No crea carpetas en disco.
    """
    return auditorias_dir() / "fotos"


def poda_pasos() -> int:
    """
    Retorna la cantidad de copias de pasos recientes a conservar.
    Lee la variable de entorno ARGENTUM_PODA_PASOS; por defecto retorna 20.
    """
    val = os.environ.get("ARGENTUM_PODA_PASOS")
    if val is not None and val.strip():
        try:
            return int(val.strip())
        except ValueError:
            return 20
    return 20
