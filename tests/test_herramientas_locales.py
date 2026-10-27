import ast
import os
from pathlib import Path
import stat
import subprocess
from types import SimpleNamespace

import pytest

from scripts.local import poda
from scripts.local.paso import ResultadoPoda, podar_backups
from scripts.local.servidor_local import asegurar_servidor_levantado


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_th1_servidor_local_status_y_start(monkeypatch, tmp_path, capsys):
    """TH.1: status 3 llama a start con -w -t 180 y timeout=200; status 0 no llama a start; TimeoutExpired y código 1 salen con 1 y texto exacto."""
    pg_ctl = tmp_path / "pg_ctl.exe"
    pg_ctl.write_text("dummy", encoding="utf-8")
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    log_file = tmp_path / "postgres.log"

    # 1) status con código 3 -> se llama a start con "-w", "-t", "180" y timeout 200
    llamadas: list[tuple[list[str], dict]] = []

    def fake_run_ok(cmd, **kwargs):
        llamadas.append((list(cmd), dict(kwargs)))
        if "status" in cmd:
            return SimpleNamespace(returncode=3, stdout="", stderr="")
        return SimpleNamespace(returncode=0, stdout="server started", stderr="")

    monkeypatch.setattr("scripts.local.servidor_local.subprocess.run", fake_run_ok)
    asegurar_servidor_levantado(
        "con_base_local", pg_ctl=pg_ctl, data_dir=data_dir, log_file=log_file
    )
    assert len(llamadas) == 2
    cmd_start, kw_start = llamadas[1]
    assert "-w" in cmd_start
    assert "-t" in cmd_start
    assert "180" in cmd_start
    assert "start" in cmd_start
    assert kw_start.get("timeout") == 200

    # 2) status 0 -> no se llama a start
    llamadas_status0: list[list[str]] = []

    def fake_run_status0(cmd, **kwargs):
        llamadas_status0.append(list(cmd))
        return SimpleNamespace(returncode=0, stdout="server is running", stderr="")

    monkeypatch.setattr("scripts.local.servidor_local.subprocess.run", fake_run_status0)
    asegurar_servidor_levantado(
        "con_base_local", pg_ctl=pg_ctl, data_dir=data_dir, log_file=log_file
    )
    assert len(llamadas_status0) == 1
    assert "status" in llamadas_status0[0]

    # 3) start que lanza TimeoutExpired -> SystemExit 1 y texto exacto de la decisión 6.a
    def fake_run_timeout(cmd, **kwargs):
        if "status" in cmd:
            return SimpleNamespace(returncode=3, stdout="", stderr="")
        raise subprocess.TimeoutExpired(cmd=cmd, timeout=200)

    monkeypatch.setattr("scripts.local.servidor_local.subprocess.run", fake_run_timeout)
    capsys.readouterr()
    with pytest.raises(SystemExit) as exc_to:
        asegurar_servidor_levantado(
            "con_base_local", pg_ctl=pg_ctl, data_dir=data_dir, log_file=log_file
        )
    assert exc_to.value.code == 1
    out_to = capsys.readouterr().out
    assert (
        "[con_base_local] ERROR: el servidor local no arrancó en 200 segundos."
        in out_to
    )

    # 4) start con código 1 -> SystemExit 1 y su texto exacto
    def fake_run_fail(cmd, **kwargs):
        if "status" in cmd:
            return SimpleNamespace(returncode=3, stdout="", stderr="")
        return SimpleNamespace(returncode=1, stdout="", stderr="fallo simulado")

    monkeypatch.setattr("scripts.local.servidor_local.subprocess.run", fake_run_fail)
    capsys.readouterr()
    with pytest.raises(SystemExit) as exc_code1:
        asegurar_servidor_levantado(
            "verificar_todo", pg_ctl=pg_ctl, data_dir=data_dir, log_file=log_file
        )
    assert exc_code1.value.code == 1
    out_code1 = capsys.readouterr().out
    assert "[verificar_todo] ERROR: pg_ctl start retornó 1" in out_code1


def test_th1_ast_scripts_no_definen_asegurar_servidor_levantado():
    """TH.1 (AST): con_base_local.py, refrescar_base_local.py y verificar_todo.py no definen asegurar_servidor_levantado."""
    for nombre in ("con_base_local.py", "refrescar_base_local.py", "verificar_todo.py"):
        ruta = REPO_ROOT / "scripts" / "local" / nombre
        tree = ast.parse(ruta.read_text(encoding="utf-8"), filename=str(ruta))
        defs = [
            node.name
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == "asegurar_servidor_levantado"
        ]
        assert defs == [], f"{nombre} no debe definir asegurar_servidor_levantado"


def _armar_21_carpetas(raiz_backups: Path) -> list[Path]:
    carpetas: list[Path] = []
    base_ts = 1_700_000_000
    for i in range(1, 22):
        carpeta = raiz_backups / f"paso_{i:02d}"
        antes = carpeta / "antes"
        despues = carpeta / "despues"
        antes.mkdir(parents=True, exist_ok=True)
        despues.mkdir(parents=True, exist_ok=True)
        (antes / "a.txt").write_text(f"antes {i}", encoding="utf-8")
        (despues / "d.txt").write_text(f"despues {i}", encoding="utf-8")
        if i == 1:
            sub = antes / "subcarpeta"
            sub.mkdir(parents=True, exist_ok=True)
            ro_file = sub / "solo_lectura.txt"
            ro_file.write_text("protegido", encoding="utf-8")
            os.chmod(ro_file, stat.S_IREAD)
        mtime = base_ts + i * 60
        os.utime(antes, (mtime, mtime))
        os.utime(despues, (mtime, mtime))
        os.utime(carpeta, (mtime, mtime))
        carpetas.append(carpeta)
    return carpetas


def test_th2_poda_archivo_solo_lectura_y_rmtree_simulado(monkeypatch, tmp_path):
    """TH.2: 21 carpetas con archivo de solo lectura en la más vieja; y rmtree simulado que no borra nada."""
    # Caso 1: borrado real con archivo de solo lectura dentro de subcarpeta en la más vieja
    b1 = tmp_path / "backups_real"
    b1.mkdir()
    carpetas_1 = _armar_21_carpetas(b1)
    mas_vieja_1 = carpetas_1[0]

    res1 = podar_backups(b1, limite_poda=20)
    assert isinstance(res1, ResultadoPoda)
    assert res1 == 1
    assert not (mas_vieja_1 / "antes").exists()
    assert not (mas_vieja_1 / "despues").exists()
    assert res1.no_podados == []
    assert res1.pasos_podados == ["paso_01"]

    # Caso 2: con shutil.rmtree simulado que no borra nada -> resultado == 0, pasos_podados vacío y no_podados con 1 elemento
    b2 = tmp_path / "backups_simulado"
    b2.mkdir()
    carpetas_2 = _armar_21_carpetas(b2)
    mas_vieja_2 = carpetas_2[0]
    ro_2 = mas_vieja_2 / "antes" / "subcarpeta" / "solo_lectura.txt"
    os.chmod(ro_2, stat.S_IWRITE)

    monkeypatch.setattr(poda.shutil, "rmtree", lambda *a, **kw: None)
    res2 = podar_backups(b2, limite_poda=20)
    assert res2 == 0
    assert res2.pasos_podados == []
    assert len(res2.no_podados) == 1
    assert res2.no_podados[0][0] == "paso_01"

