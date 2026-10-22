"""
Tests para herramientas de rutas, copia segura, duración, poda y auditorías.
Ubicación: tests/test_paso_rutas.py
"""
from __future__ import annotations

import importlib
import os
from pathlib import Path
import shutil
import subprocess
from datetime import datetime, timezone, timedelta
import pytest


def test_ta1_rutas_dentro_de_tmp_path(tmp_path, monkeypatch):
    """TA.1: con ARGENTUM_AUDITORIAS_DIR = tmp_path, raw_dir("x"), backups_dir("x") y fotos_dir() quedan dentro de tmp_path; importar rutas.py no crea ninguna carpeta."""
    aud_path = tmp_path / "aud_test"
    monkeypatch.setenv("ARGENTUM_AUDITORIAS_DIR", str(aud_path))

    import scripts.local.rutas as rutas
    importlib.reload(rutas)

    # Verificar que importar no creó carpetas
    assert not aud_path.exists()

    r = rutas.raw_dir("x")
    b = rutas.backups_dir("x")
    f = rutas.fotos_dir()

    assert r == aud_path / "raw" / "x"
    assert b == aud_path / "backups" / "x"
    assert f == aud_path / "fotos"
    assert not aud_path.exists()


def test_ta2_copia_excluye_assets_y_archivos_grandes(tmp_path):
    """TA.2: en un src falso con assets/a.svg, b/c.ts de 900 KB y b/d.json de 2 MB, la copia contiene solo b/c.ts."""
    origen = tmp_path / "origen"
    src_dir = origen / "src"
    src_dir.mkdir(parents=True)

    # assets/a.svg
    assets_dir = src_dir / "assets"
    assets_dir.mkdir()
    (assets_dir / "a.svg").write_text("<svg></svg>", encoding="utf-8")

    # b/c.ts de 900 KB
    b_dir = src_dir / "b"
    b_dir.mkdir()
    (b_dir / "c.ts").write_bytes(b"x" * (900 * 1024))

    # b/d.json de 2 MB
    (b_dir / "d.json").write_bytes(b"y" * (2 * 1024 * 1024))

    destino = tmp_path / "destino"
    from scripts.local.paso import realizar_copia_segura
    realizar_copia_segura(origen, destino, ["src"])

    dst_src = destino / "src"
    archivos_copiados = sorted([p.relative_to(dst_src).as_posix() for p in dst_src.rglob("*") if p.is_file()])

    assert archivos_copiados == ["b/c.ts"]


def test_ta3_poda_backups(tmp_path):
    """TA.3: con 25 carpetas backups\\pN\\ y poda_pasos=20: quedan antes y despues solo en las 20 más recientes; los 25 respaldo.json existen; una carpeta raw\\pN\\ falsa queda intacta; la función devuelve 5."""
    backups_dir = tmp_path / "backups"
    raw_dir = tmp_path / "raw"
    backups_dir.mkdir()
    raw_dir.mkdir()

    base_time = 1700000000.0

    # 25 carpetas backups/pN/ escalonadas
    for i in range(1, 26):
        p_dir = backups_dir / f"p{i:02d}"
        antes = p_dir / "antes"
        desp = p_dir / "despues"
        antes.mkdir(parents=True)
        desp.mkdir(parents=True)
        (antes / "archivo.txt").write_text("antes", encoding="utf-8")
        (desp / "archivo.txt").write_text("desp", encoding="utf-8")
        (p_dir / "respaldo.json").write_text('{"respaldo": true}', encoding="utf-8")

        # Escalonar mtime: p01 es el más antiguo, p25 el más reciente
        mtime = base_time + (i * 100)
        os.utime(antes, (mtime, mtime))
        os.utime(desp, (mtime, mtime))

    # Carpeta raw/p01/ falsa para verificar que queda intacta
    raw_p01 = raw_dir / "p01"
    raw_p01.mkdir()
    (raw_p01 / "dato.txt").write_text("crudo", encoding="utf-8")

    from scripts.local.paso import podar_backups
    resultado = podar_backups(backups_dir, limite_poda=20)

    # La función devuelve 5
    assert resultado == 5

    # Los 25 respaldo.json existen
    for i in range(1, 26):
        assert (backups_dir / f"p{i:02d}" / "respaldo.json").exists()

    # raw/pN/ falsa queda intacta
    assert (raw_p01 / "dato.txt").exists()
    assert (raw_p01 / "dato.txt").read_text(encoding="utf-8") == "crudo"

    # Quedan antes y despues solo en las 20 más recientes (p06 a p25)
    for i in range(1, 6):
        p_dir = backups_dir / f"p{i:02d}"
        assert not (p_dir / "antes").exists()
        assert not (p_dir / "despues").exists()

    for i in range(6, 26):
        p_dir = backups_dir / f"p{i:02d}"
        assert (p_dir / "antes").exists()
        assert (p_dir / "despues").exists()


def test_ta4_verificar_duracion():
    """TA.4: verificar_duracion('2026-10-21 23:50:00 -0300', ahora=22/10 00:10) es True; ('2026-10-21 08:00:00 -0300', 21/10 21:00) es True; ('2026-10-21 08:00:00 -0300', 22/10 09:00) es False con texto exacto."""
    from scripts.local.paso import verificar_duracion

    tz_ar = timezone(timedelta(hours=-3))

    # Caso 1: 21/10 23:50 a 22/10 00:10 (20 min) -> True
    ahora_1 = datetime(2026, 10, 22, 0, 10, tzinfo=tz_ar)
    assert bool(verificar_duracion("2026-10-21 23:50:00 -0300", ahora=ahora_1)) is True

    # Caso 2: 21/10 08:00 -0300 a 21/10 21:00 UTC (10 horas) -> True
    ahora_2 = datetime(2026, 10, 21, 21, 0, tzinfo=timezone.utc)
    assert bool(verificar_duracion("2026-10-21 08:00:00 -0300", ahora=ahora_2)) is True

    # Caso 3: 21/10 08:00 -0300 a 22/10 09:00 UTC (22 horas) -> False con texto exacto
    ahora_3 = datetime(2026, 10, 22, 9, 0, tzinfo=timezone.utc)
    res_3 = verificar_duracion("2026-10-21 08:00:00 -0300", ahora=ahora_3)
    assert bool(res_3) is False
    ok_3, msg_3 = res_3
    assert ok_3 is False
    assert msg_3 == "Pasaron más de 12 horas desde el inicio del paso."


def _obtener_rutas_repos() -> tuple[Path, Path | None]:
    """Obtiene la ruta a backend y frontend de forma portable (CI o local)."""
    backend_dir = Path(__file__).resolve().parent.parent
    candidatos_frontend = [
        backend_dir.parent / "argentum-frontend",
        Path(r"C:\Users\sebas\OneDrive\Documentos\GitHub\argentum-frontend"),
    ]
    frontend_dir = None
    for cand in candidatos_frontend:
        if cand.exists():
            frontend_dir = cand
            break
    return backend_dir, frontend_dir


def test_ta5_estatica_sin_rutas_viejas_auditorias():
    """TA.5 (estática): en los .py y .mjs de los dos repos (sin venv, node_modules ni graphify-out) no aparece 'GitHub\\auditorias', 'GitHub/auditorias' ni '../auditorias'."""
    patron_1 = "Git" + r"Hub\auditorias"
    patron_2 = "Git" + "Hub/auditorias"
    patron_3 = ".." + "/auditorias"
    patrones_prohibidos = [patron_1, patron_2, patron_3]

    backend_dir, frontend_dir = _obtener_rutas_repos()
    repos = [backend_dir]
    if frontend_dir is not None and frontend_dir.exists():
        repos.append(frontend_dir)

    ignorar = {".git", "venv", "node_modules", "graphify-out", "__pycache__", ".pytest_cache", "dist"}
    archivo_propio = Path(__file__).resolve()

    fallos = []
    for repo in repos:
        for root, dirs, files in os.walk(repo):
            dirs[:] = [d for d in dirs if d not in ignorar]
            for f in files:
                if f.endswith(".py") or f.endswith(".mjs"):
                    p = Path(root) / f
                    if p.resolve() == archivo_propio:
                        continue
                    try:
                        content = p.read_text(encoding="utf-8", errors="replace")
                        for pat in patrones_prohibidos:
                            if pat in content:
                                fallos.append(f"{p}: contiene '{pat}'")
                    except Exception as e:
                        fallos.append(f"{p}: error al leer ({e})")

    assert len(fallos) == 0, "Se encontraron patrones de rutas viejas de auditorías:\n" + "\n".join(fallos)


def test_ta6_audit_design_system_falla_si_no_existe_auditorias(tmp_path):
    """TA.6: correr node scripts/audit-design-system.mjs con ARGENTUM_AUDITORIAS_DIR apuntando a una carpeta que no existe termina con código 1, stderr empieza con 'No existe la carpeta de auditorías: ' y la carpeta sigue sin existir."""
    backend_dir, frontend_dir = _obtener_rutas_repos()
    if frontend_dir is None or not (frontend_dir / "scripts" / "audit-design-system.mjs").exists():
        pytest.skip("argentum-frontend no está presente en este entorno")

    import shutil
    if shutil.which("node") is None:
        pytest.skip("node no está disponible en este entorno")

    carpeta_inexistente = tmp_path / "auditorias_no_existe"
    env = os.environ.copy()
    env["ARGENTUM_AUDITORIAS_DIR"] = str(carpeta_inexistente)

    res = subprocess.run(
        ["node", "scripts/audit-design-system.mjs"],
        cwd=str(frontend_dir),
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        shell=(os.name == "nt"),
    )

    assert res.returncode == 1
    assert res.stderr.startswith("No existe la carpeta de auditorías: ")
    assert not carpeta_inexistente.exists()


def test_ta7_archivos_eliminados():
    """TA.7: Path('app/schemas/tarjeta_credito 2.py') no existe y src/assets/icons/categorias_raw no existe."""
    backend_dir, frontend_dir = _obtener_rutas_repos()

    p_schema = backend_dir / "app" / "schemas" / "tarjeta_credito 2.py"
    assert not p_schema.exists()

    if frontend_dir is not None and frontend_dir.exists():
        p_categorias = frontend_dir / "src" / "assets" / "icons" / "categorias_raw"
        assert not p_categorias.exists()
