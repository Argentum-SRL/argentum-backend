"""
Utilidad de poda de copias de respaldo de pasos anteriores (Decisión 6.b).
"""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import stat


class ResultadoPoda(int):
    """
    Resultado numérico de copias podadas que expone también la lista de pasos podados
    y la lista de pasos no podados con su primer error.
    """

    def __new__(
        cls,
        cantidad: int,
        pasos_podados: list[str],
        no_podados: list[tuple[str, str]] | None = None,
    ):
        obj = super().__new__(cls, cantidad)
        obj.pasos_podados = pasos_podados
        obj.no_podados = list(no_podados) if no_podados is not None else []
        return obj

    def __iter__(self):
        return iter((int(self), self.pasos_podados))


def _ruta_larga_windows(ruta: Path) -> str:
    """Agrega el prefijo \\\\?\\ en Windows para soportar rutas largas."""
    s = str(ruta.resolve())
    if os.name == "nt" and not s.startswith("\\\\?\\"):
        return "\\\\?\\" + s
    return s


def _borrar_arbol_con_reintento(carpeta: Path, errores: list[str]) -> None:
    """
    Borra una carpeta con shutil.rmtree(..., onexc=...).
    Ante un fallo de permiso/solo lectura, quita el atributo de solo lectura
    (os.chmod(ruta, stat.S_IWRITE)) y reintenta una vez.
    """
    reintentos: set[str] = set()

    def _onexc(func, path, exc):
        clave = f"{getattr(func, '__name__', str(func))}:{path}"
        if clave not in reintentos:
            reintentos.add(clave)
            try:
                os.chmod(path, stat.S_IWRITE)
                func(path)
                return
            except Exception as retry_exc:
                if not errores:
                    errores.append(str(retry_exc))
                return
        if not errores:
            errores.append(str(exc))

    objetivo = _ruta_larga_windows(carpeta)
    shutil.rmtree(objetivo, onexc=_onexc)


def podar_backups(backups_dir_path: Path, limite_poda: int = 20) -> ResultadoPoda:
    """
    Si limite_poda > 0, busca carpetas backups/<paso>/ que tengan subcarpeta 'antes' o 'despues'.
    Las ordena por fecha de modificación (st_mtime) más reciente de esas subcarpetas.
    Conserva las limite_poda más recientes y en las demás borra exclusivamente 'antes' y 'despues'.
    Una carpeta cuenta como podada solo si después no existe ni 'antes' ni 'despues'.
    Retorna ResultadoPoda(cantidad_podadas, pasos_podados, no_podados).
    """
    if limite_poda <= 0 or not backups_dir_path.exists():
        return ResultadoPoda(0, [], [])

    candidatos = []
    for p in backups_dir_path.iterdir():
        if not p.is_dir():
            continue
        antes_path = p / "antes"
        desp_path = p / "despues"
        tiene_antes = antes_path.is_dir()
        tiene_desp = desp_path.is_dir()
        if not tiene_antes and not tiene_desp:
            continue

        mtime_antes = antes_path.stat().st_mtime if tiene_antes else 0.0
        mtime_desp = desp_path.stat().st_mtime if tiene_desp else 0.0
        mtime_mas_reciente = max(mtime_antes, mtime_desp)
        candidatos.append((mtime_mas_reciente, p))

    # Ordenar de más reciente a más antiguo
    candidatos.sort(key=lambda x: x[0], reverse=True)

    a_podar = candidatos[limite_poda:]
    pasos_podados: list[str] = []
    no_podados: list[tuple[str, str]] = []

    for _, p in a_podar:
        antes_path = p / "antes"
        desp_path = p / "despues"
        errores_paso: list[str] = []

        if antes_path.is_dir():
            _borrar_arbol_con_reintento(antes_path, errores_paso)
        if desp_path.is_dir():
            _borrar_arbol_con_reintento(desp_path, errores_paso)

        sigue_existiendo = antes_path.exists() or desp_path.exists()
        if not sigue_existiendo:
            pasos_podados.append(p.name)
        else:
            motivo = errores_paso[0] if errores_paso else "La carpeta sigue existiendo tras rmtree"
            no_podados.append((p.name, motivo))

    return ResultadoPoda(len(pasos_podados), pasos_podados, no_podados)
