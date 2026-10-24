"""
Pruebas de producción y entorno local (P.1 a P.14).
Ubicación: tests/test_produccion_local.py
"""
import hashlib
import os
import sys
from pathlib import Path
import pytest

from scripts.local import rutas
from scripts.local import produccion
from scripts.local import con_produccion
from scripts.local import separar_produccion
from scripts.local.refrescar_base_local import formatear_mensaje_migraciones


def test_p1_url_para_refresco_con_produccion_env(tmp_path, monkeypatch, capsys):
    # P.1 url_para_refresco con produccion.env válido devuelve su URL sin aviso,
    # aunque el .env tenga otra URL de producción; capsys no contiene la URL ni la contraseña.
    secretos_dir = tmp_path / "secretos"
    secretos_dir.mkdir()
    prod_env = secretos_dir / "produccion.env"
    prod_url = "postgresql://prod_user:secret_pwd@db.produccion.com:5432/prod_db"
    prod_env.write_text(f"PRODUCCION_DATABASE_URL={prod_url}\n", encoding="utf-8")

    backend_env = tmp_path / ".env"
    other_prod_url = "postgresql://other_user:other_pwd@other.prod.com:5432/other_db"
    backend_env.write_text(f"DATABASE_URL={other_prod_url}\n", encoding="utf-8")

    monkeypatch.setattr(rutas, "secretos_dir", lambda: secretos_dir)
    monkeypatch.setattr(produccion, "produccion_env_path", lambda: prod_env)
    monkeypatch.setattr(produccion, "backend_env_path", lambda: backend_env)

    url_res = produccion.url_para_refresco()
    assert url_res == prod_url

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""
    assert "secret_pwd" not in captured.out
    assert "other_pwd" not in captured.out


def test_p2_url_para_refresco_sin_produccion_env(tmp_path, monkeypatch, capsys):
    # P.2 Sin produccion.env y con .env de producción: url_para_refresco devuelve la del .env con el AVISO exacto;
    # url_para_comando da el error "Falta ..." con la ruta de tmp_path.
    secretos_dir = tmp_path / "secretos"
    prod_env = secretos_dir / "produccion.env"

    backend_env = tmp_path / ".env"
    prod_url = "postgresql://user:pass@db.real.com:5432/real_db"
    backend_env.write_text(f"DATABASE_URL={prod_url}\n", encoding="utf-8")

    monkeypatch.setattr(rutas, "secretos_dir", lambda: secretos_dir)
    monkeypatch.setattr(produccion, "produccion_env_path", lambda: prod_env)
    monkeypatch.setattr(produccion, "backend_env_path", lambda: backend_env)

    url_res = produccion.url_para_refresco()
    assert url_res == prod_url
    captured = capsys.readouterr()
    assert (
        "AVISO: la URL de producción todavía está en el .env (falta correr separar_produccion.py)."
        in captured.out
    )

    with pytest.raises(FileNotFoundError) as exc:
        produccion.url_para_comando()
    assert f"Falta {prod_env} (se crea con scripts\\local\\separar_produccion.py)." in str(exc.value)


def test_p3_sin_produccion_env_y_env_local(tmp_path, monkeypatch):
    # P.3 Sin produccion.env y con .env local: url_para_refresco da "No hay URL de producción para el refresco."
    secretos_dir = tmp_path / "secretos"
    prod_env = secretos_dir / "produccion.env"

    backend_env = tmp_path / ".env"
    local_url = "postgresql://postgres:pass@localhost:5433/argentum_local"
    backend_env.write_text(f"DATABASE_URL={local_url}\n", encoding="utf-8")

    monkeypatch.setattr(rutas, "secretos_dir", lambda: secretos_dir)
    monkeypatch.setattr(produccion, "produccion_env_path", lambda: prod_env)
    monkeypatch.setattr(produccion, "backend_env_path", lambda: backend_env)

    with pytest.raises(ValueError, match="No hay URL de producción para el refresco."):
        produccion.url_para_refresco()


def test_p4_produccion_env_con_url_local(tmp_path, monkeypatch):
    # P.4 produccion.env con URL local: "La URL de produccion.env apunta a una base local." en url_para_refresco y en url_para_comando.
    secretos_dir = tmp_path / "secretos"
    secretos_dir.mkdir()
    prod_env = secretos_dir / "produccion.env"
    local_url = "postgresql://postgres:pass@127.0.0.1:5433/argentum_local"
    prod_env.write_text(f"PRODUCCION_DATABASE_URL={local_url}\n", encoding="utf-8")

    monkeypatch.setattr(rutas, "secretos_dir", lambda: secretos_dir)
    monkeypatch.setattr(produccion, "produccion_env_path", lambda: prod_env)

    with pytest.raises(ValueError, match="La URL de produccion.env apunta a una base local."):
        produccion.url_para_refresco()

    with pytest.raises(ValueError, match="La URL de produccion.env apunta a una base local."):
        produccion.url_para_comando()


def test_p5_con_produccion_sin_env_y_sin_terminal(tmp_path, monkeypatch, capsys):
    # P.5 con_produccion sin produccion.env: código 2 y el comando no corre (un archivo marcador no se crea).
    # Con produccion.env y stdin que no es terminal: código 2 "Este comando se corre solo a mano desde PowerShell." y el comando no corre.
    marker = tmp_path / "marker.txt"
    cmd = [sys.executable, "-c", f"open(r'{marker}', 'w').write('ok')"]

    secretos_dir = tmp_path / "secretos"
    prod_env = secretos_dir / "produccion.env"
    monkeypatch.setattr(rutas, "secretos_dir", lambda: secretos_dir)
    monkeypatch.setattr(produccion, "produccion_env_path", lambda: prod_env)
    monkeypatch.setattr(sys, "argv", ["con_produccion.py"] + cmd)

    with pytest.raises(SystemExit) as exc:
        con_produccion.main()
    assert exc.value.code == 2
    assert not marker.exists()

    # Ahora creamos produccion.env pero stdin no es terminal
    secretos_dir.mkdir(parents=True, exist_ok=True)
    prod_url = "postgresql://u:p@db.prod.com:5432/x"
    prod_env.write_text(f"PRODUCCION_DATABASE_URL={prod_url}\n", encoding="utf-8")

    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    with pytest.raises(SystemExit) as exc2:
        con_produccion.main()
    assert exc2.value.code == 2
    assert not marker.exists()

    captured = capsys.readouterr()
    assert "Este comando se corre solo a mano desde PowerShell." in captured.out


def test_p6_con_produccion_cancelacion(tmp_path, monkeypatch, capsys):
    # P.6 Con terminal simulada y los textos "produccion", "SI" y fin de entrada:
    # código 2 "Cancelado: no se corrió nada." y el comando no corre.
    marker = tmp_path / "marker.txt"
    cmd = [sys.executable, "-c", f"open(r'{marker}', 'w').write('ok')"]

    secretos_dir = tmp_path / "secretos"
    secretos_dir.mkdir(parents=True, exist_ok=True)
    prod_env = secretos_dir / "produccion.env"
    prod_url = "postgresql://u:p@db.prod.com:5432/x"
    prod_env.write_text(f"PRODUCCION_DATABASE_URL={prod_url}\n", encoding="utf-8")

    monkeypatch.setattr(rutas, "secretos_dir", lambda: secretos_dir)
    monkeypatch.setattr(produccion, "produccion_env_path", lambda: prod_env)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(sys, "argv", ["con_produccion.py"] + cmd)

    # Caso 1: "produccion" (minúsculas)
    monkeypatch.setattr("builtins.input", lambda prompt="": "produccion")
    with pytest.raises(SystemExit) as exc1:
        con_produccion.main()
    assert exc1.value.code == 2
    assert not marker.exists()

    # Caso 2: "SI"
    monkeypatch.setattr("builtins.input", lambda prompt="": "SI")
    with pytest.raises(SystemExit) as exc2:
        con_produccion.main()
    assert exc2.value.code == 2
    assert not marker.exists()

    # Caso 3: fin de entrada (EOFError)
    def raise_eof(prompt=""):
        raise EOFError()

    monkeypatch.setattr("builtins.input", raise_eof)
    with pytest.raises(SystemExit) as exc3:
        con_produccion.main()
    assert exc3.value.code == 2
    assert not marker.exists()

    captured = capsys.readouterr()
    assert "Cancelado: no se corrió nada." in captured.out


def test_p7_con_produccion_ejecucion_escritura(tmp_path, monkeypatch):
    # P.7 Con "PRODUCCION": corre un script de prueba (sys.executable) que escribe en un archivo lo que recibe:
    # DATABASE_URL igual a la de produccion.env de prueba, ARGENTUM_PRODUCCION_CONFIRMADA "1" y sin ARGENTUM_PRODUCCION_SOLO_LECTURA;
    # registro_produccion.log (en ARGENTUM_AUDITORIAS_DIR de prueba) suma 1 línea con ESCRITURA y el código 0, sin la URL ni la contraseña.
    secretos_dir = tmp_path / "secretos"
    secretos_dir.mkdir(parents=True, exist_ok=True)
    prod_env = secretos_dir / "produccion.env"
    prod_url = "postgresql://prod_u:secret_p@db.test.com:5432/x"
    prod_env.write_text(f"PRODUCCION_DATABASE_URL={prod_url}\n", encoding="utf-8")

    auditorias_dir = tmp_path / "auditorias"
    auditorias_dir.mkdir(parents=True, exist_ok=True)

    marker = tmp_path / "received_env.json"
    worker_script = tmp_path / "worker.py"
    worker_script.write_text(
        "import os, json\n"
        f"with open(r'{marker}', 'w') as f:\n"
        "    json.dump({\n"
        "        'DATABASE_URL': os.environ.get('DATABASE_URL'),\n"
        "        'ARGENTUM_PRODUCCION_CONFIRMADA': os.environ.get('ARGENTUM_PRODUCCION_CONFIRMADA'),\n"
        "        'ARGENTUM_PRODUCCION_SOLO_LECTURA': os.environ.get('ARGENTUM_PRODUCCION_SOLO_LECTURA'),\n"
        "    }, f)\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(rutas, "secretos_dir", lambda: secretos_dir)
    monkeypatch.setattr(rutas, "auditorias_dir", lambda: auditorias_dir)
    monkeypatch.setattr(produccion, "produccion_env_path", lambda: prod_env)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda prompt="": "PRODUCCION")
    monkeypatch.setattr(sys, "argv", ["con_produccion.py", sys.executable, str(worker_script)])

    with pytest.raises(SystemExit) as exc:
        con_produccion.main()
    assert exc.value.code == 0

    import json
    data = json.loads(marker.read_text(encoding="utf-8"))
    assert data["DATABASE_URL"] == prod_url
    assert data["ARGENTUM_PRODUCCION_CONFIRMADA"] == "1"
    assert data["ARGENTUM_PRODUCCION_SOLO_LECTURA"] is None

    log_file = auditorias_dir / "registro_produccion.log"
    assert log_file.exists()
    log_content = log_file.read_text(encoding="utf-8")
    log_lines = log_content.strip().splitlines()
    assert len(log_lines) == 1
    assert "ESCRITURA" in log_lines[0]
    assert "codigo: 0" in log_lines[0]
    assert "secret_p" not in log_lines[0]
    assert "postgresql://" not in log_lines[0]


def test_p8_con_produccion_solo_lectura(tmp_path, monkeypatch):
    # P.8 --solo-lectura con "LECTURA": ARGENTUM_PRODUCCION_SOLO_LECTURA "1" y la línea del registro con LECTURA.
    secretos_dir = tmp_path / "secretos"
    secretos_dir.mkdir(parents=True, exist_ok=True)
    prod_env = secretos_dir / "produccion.env"
    prod_url = "postgresql://prod_u:secret_p@db.test.com:5432/x"
    prod_env.write_text(f"PRODUCCION_DATABASE_URL={prod_url}\n", encoding="utf-8")

    auditorias_dir = tmp_path / "auditorias"
    auditorias_dir.mkdir(parents=True, exist_ok=True)

    marker = tmp_path / "received_env.json"
    worker_script = tmp_path / "worker.py"
    worker_script.write_text(
        "import os, json\n"
        f"with open(r'{marker}', 'w') as f:\n"
        "    json.dump({\n"
        "        'ARGENTUM_PRODUCCION_SOLO_LECTURA': os.environ.get('ARGENTUM_PRODUCCION_SOLO_LECTURA'),\n"
        "    }, f)\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(rutas, "secretos_dir", lambda: secretos_dir)
    monkeypatch.setattr(rutas, "auditorias_dir", lambda: auditorias_dir)
    monkeypatch.setattr(produccion, "produccion_env_path", lambda: prod_env)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda prompt="": "LECTURA")
    monkeypatch.setattr(
        sys,
        "argv",
        ["con_produccion.py", "--solo-lectura", sys.executable, str(worker_script)],
    )

    with pytest.raises(SystemExit) as exc:
        con_produccion.main()
    assert exc.value.code == 0

    import json
    data = json.loads(marker.read_text(encoding="utf-8"))
    assert data["ARGENTUM_PRODUCCION_SOLO_LECTURA"] == "1"

    log_file = auditorias_dir / "registro_produccion.log"
    log_content = log_file.read_text(encoding="utf-8")
    assert "LECTURA" in log_content


def test_p9_separar_produccion_primera_corrida(tmp_path, monkeypatch, capsys):
    # P.9 separar_produccion con un .env de 5 líneas con final CRLF (una es DATABASE_URL de producción) y un pg_local.env de prueba:
    # las otras 4 líneas quedan byte a byte iguales; DATABASE_URL=postgresql://postgres:<PGPASSWORD de prueba>@localhost:5433/argentum_local con CRLF;
    # produccion.env con PRODUCCION_DATABASE_URL=<la URL vieja>; el respaldo tiene el mismo SHA-256 que el .env original;
    # la salida no contiene la URL vieja ni ninguna contraseña.
    secretos_dir = tmp_path / "secretos"
    monkeypatch.setattr(rutas, "secretos_dir", lambda: secretos_dir)

    pg_env = tmp_path / "pg_local.env"
    pg_password = "test_pg_password_123"
    pg_env.write_text(f"PGPASSWORD={pg_password}\n", encoding="utf-8")
    monkeypatch.setattr(rutas, "pg_local_env", lambda: pg_env)

    old_url = "postgresql://user_prod:pass_prod_999@db.prod.com:5432/orig_db"
    lines = [
        "ENVIRONMENT=development\r\n",
        "SECRET_KEY=secret_12345\r\n",
        f"DATABASE_URL={old_url}\r\n",
        "ENABLE_SCHEDULER=true\r\n",
        "FRONTEND_URL=http://localhost:5173\r\n",
    ]
    orig_content = "".join(lines).encode("utf-8")
    orig_sha256 = hashlib.sha256(orig_content).hexdigest()

    backend_env = tmp_path / ".env"
    backend_env.write_bytes(orig_content)
    monkeypatch.setattr(separar_produccion, "BACKEND_DIR", tmp_path)
    monkeypatch.setattr(sys, "argv", ["separar_produccion.py"])

    separar_produccion.main()

    # Verificar respaldo
    respaldos = list(secretos_dir.glob("env_respaldo_*.env"))
    assert len(respaldos) == 1
    respaldo_sha256 = hashlib.sha256(respaldos[0].read_bytes()).hexdigest()
    assert respaldo_sha256 == orig_sha256

    # Verificar produccion.env
    prod_env = secretos_dir / "produccion.env"
    assert prod_env.exists()
    assert f"PRODUCCION_DATABASE_URL={old_url}" in prod_env.read_text(encoding="utf-8")

    # Verificar .env nuevo byte a byte en las otras 4 líneas
    new_bytes = backend_env.read_bytes()
    new_lines = new_bytes.decode("utf-8").splitlines(keepends=True)
    assert len(new_lines) == 5
    assert new_lines[0] == lines[0]
    assert new_lines[1] == lines[1]
    assert new_lines[2] == f"DATABASE_URL=postgresql://postgres:{pg_password}@localhost:5433/argentum_local\r\n"
    assert new_lines[3] == lines[3]
    assert new_lines[4] == lines[4]

    captured = capsys.readouterr()
    assert old_url not in captured.out
    assert "pass_prod_999" not in captured.out
    assert pg_password not in captured.out
    assert "OK: la URL de producción quedó en" in captured.out
    assert "OK: el .env ahora apunta a la copia local" in captured.out


def test_p10_separar_produccion_segunda_corrida(tmp_path, monkeypatch):
    # P.10 Segunda corrida: código 2 "El .env ya apunta a la copia local: no hay nada que separar." y ningún archivo cambia (SHA-256 iguales).
    # Con produccion.env ya creado y un .env de producción: código 2 "Ya existe produccion.env: no se toca."
    secretos_dir = tmp_path / "secretos"
    secretos_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(rutas, "secretos_dir", lambda: secretos_dir)

    pg_env = tmp_path / "pg_local.env"
    pg_env.write_text("PGPASSWORD=pass\n", encoding="utf-8")
    monkeypatch.setattr(rutas, "pg_local_env", lambda: pg_env)

    # Caso A: .env ya apunta a local
    backend_env = tmp_path / ".env"
    local_env_content = b"DATABASE_URL=postgresql://postgres:pass@localhost:5433/argentum_local\r\n"
    backend_env.write_bytes(local_env_content)
    orig_sha = hashlib.sha256(local_env_content).hexdigest()

    monkeypatch.setattr(separar_produccion, "BACKEND_DIR", tmp_path)
    monkeypatch.setattr(sys, "argv", ["separar_produccion.py"])

    with pytest.raises(SystemExit) as exc1:
        separar_produccion.main()
    assert exc1.value.code == 2
    assert hashlib.sha256(backend_env.read_bytes()).hexdigest() == orig_sha

    # Caso B: .env apunta a producción pero produccion.env ya existe
    prod_env = secretos_dir / "produccion.env"
    prod_env.write_text("PRODUCCION_DATABASE_URL=postgresql://u:p@db:5432/x\n", encoding="utf-8")
    backend_env.write_text("DATABASE_URL=postgresql://u:p@db:5432/x\n", encoding="utf-8")

    with pytest.raises(SystemExit) as exc2:
        separar_produccion.main()
    assert exc2.value.code == 2


def test_p11_separar_produccion_revertir(tmp_path, monkeypatch):
    # P.11 --revertir <respaldo>: el .env queda con el SHA-256 original y produccion.env pasa a produccion.env.revertido_<fecha> (el archivo sigue existiendo con otro nombre).
    secretos_dir = tmp_path / "secretos"
    secretos_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(rutas, "secretos_dir", lambda: secretos_dir)

    orig_content = b"DATABASE_URL=postgresql://user:pass@db.orig.com:5432/orig\r\n"
    orig_sha = hashlib.sha256(orig_content).hexdigest()
    respaldo_file = secretos_dir / "env_respaldo_20261024_120000.env"
    respaldo_file.write_bytes(orig_content)

    backend_env = tmp_path / ".env"
    backend_env.write_bytes(b"DATABASE_URL=postgresql://postgres:x@localhost:5433/argentum_local\r\n")

    prod_env = secretos_dir / "produccion.env"
    prod_env.write_text("PRODUCCION_DATABASE_URL=postgresql://user:pass@db.orig.com:5432/orig\n", encoding="utf-8")

    monkeypatch.setattr(separar_produccion, "BACKEND_DIR", tmp_path)
    monkeypatch.setattr(sys, "argv", ["separar_produccion.py", "--revertir", str(respaldo_file)])

    with pytest.raises(SystemExit) as exc:
        separar_produccion.main()
    assert exc.value.code == 0

    assert hashlib.sha256(backend_env.read_bytes()).hexdigest() == orig_sha
    assert not prod_env.exists()
    revertidos = list(secretos_dir.glob("produccion.env.revertido_*"))
    assert len(revertidos) == 1
    assert revertidos[0].exists()


def test_p12_estatica_cero_fugas():
    # P.12 Estática: ningún archivo de app/ ni de scripts/, salvo scripts/local/produccion.py y scripts/local/separar_produccion.py,
    # contiene "produccion.env" ni "PRODUCCION_DATABASE_URL".
    # Ningún .py de app/, scripts/ ni tests/ contiene la ruta fija a C: / argentum_local.
    backend_root = Path(__file__).resolve().parents[1]

    # Armamos textos por partes para no auto-encontrarnos
    token_p_env = "produccion" + ".env"
    token_p_db_url = "PRODUCCION" + "_DATABASE_URL"
    pat_c1 = "C:" + chr(92) + "argentum_local"
    pat_c2 = "C:" + "/" + "argentum_local"

    archivos_permitidos_tokens = {
        (backend_root / "scripts" / "local" / "produccion.py").resolve(),
        (backend_root / "scripts" / "local" / "separar_produccion.py").resolve(),
    }

    # Revisar app/ y scripts/ por produccion.env y PRODUCCION_DATABASE_URL
    for base_folder in ("app", "scripts"):
        for root, _, files in os.walk(backend_root / base_folder):
            if "__pycache__" in root:
                continue
            for f in files:
                if f.endswith(".pyc") or f.endswith(".pyo"):
                    continue
                fpath = Path(root) / f
                if fpath.resolve() in archivos_permitidos_tokens:
                    continue
                try:
                    text = fpath.read_text(encoding="utf-8", errors="replace")
                except Exception:
                    continue
                assert token_p_env not in text, f"Fuga de {token_p_env} en {fpath}"
                assert token_p_db_url not in text, f"Fuga de {token_p_db_url} en {fpath}"

    # Revisar app/, scripts/ y tests/ por rutas a base local en C
    self_path = Path(__file__).resolve()
    for base_folder in ("app", "scripts", "tests"):
        for root, _, files in os.walk(backend_root / base_folder):
            if "__pycache__" in root:
                continue
            for f in files:
                if not f.endswith(".py"):
                    continue
                fpath = Path(root) / f
                if fpath.resolve() == self_path:
                    continue
                try:
                    text = fpath.read_text(encoding="utf-8", errors="replace")
                except Exception:
                    continue
                assert pat_c1.lower() not in text.lower(), f"Ruta fija {pat_c1} en {fpath}"
                assert pat_c2.lower() not in text.lower(), f"Ruta fija {pat_c2} en {fpath}"


def test_p13_rutas(monkeypatch):
    # P.13 rutas: base_local_dir() es D:\Argentum\base_local por defecto y respeta ARGENTUM_BASE_LOCAL_DIR;
    # secretos_dir() es D:\Argentum\secretos por defecto y respeta ARGENTUM_SECRETOS_DIR;
    # pgsql_dir, data_dir, log_pg, pg_local_env y dumps_dir quedan dentro de base_local_dir().
    monkeypatch.delenv("ARGENTUM_BASE_LOCAL_DIR", raising=False)
    monkeypatch.delenv("ARGENTUM_SECRETOS_DIR", raising=False)

    assert rutas.base_local_dir() == Path(r"D:\Argentum\base_local")
    assert rutas.secretos_dir() == Path(r"D:\Argentum\secretos")

    b_dir = rutas.base_local_dir()
    assert rutas.pgsql_dir() == b_dir / "pgsql"
    assert rutas.data_dir() == b_dir / "data"
    assert rutas.log_pg() == b_dir / "pg.log"
    assert rutas.pg_local_env() == b_dir / "pg_local.env"
    assert rutas.dumps_dir() == b_dir / "dumps"

    monkeypatch.setenv("ARGENTUM_BASE_LOCAL_DIR", r"E:\Custom\base")
    monkeypatch.setenv("ARGENTUM_SECRETOS_DIR", r"E:\Custom\secretos")

    assert rutas.base_local_dir() == Path(r"E:\Custom\base")
    assert rutas.secretos_dir() == Path(r"E:\Custom\secretos")
    assert rutas.pgsql_dir() == Path(r"E:\Custom\base") / "pgsql"
    assert rutas.dumps_dir() == Path(r"E:\Custom\base") / "dumps"


def test_p14_mensaje_migraciones():
    # P.14 La función pura de la decisión 6: con revisiones iguales devuelve
    # "Migraciones: producción y copia local en a4e5f6a7b8c9.";
    # distintas, la línea de MIGRACIÓN PENDIENTE con las dos; ninguna contiene FALLO, ERROR, DESVIO ni INESPERADA.
    msg_igual = formatear_mensaje_migraciones("a4e5f6a7b8c9", "a4e5f6a7b8c9")
    assert msg_igual == "Migraciones: producción y copia local en a4e5f6a7b8c9."
    for prohibido in ("FALLO", "ERROR", "DESVIO", "INESPERADA"):
        assert prohibido not in msg_igual

    msg_dist = formatear_mensaje_migraciones("rev_p_111", "rev_l_222")
    assert (
        msg_dist
        == "MIGRACIÓN PENDIENTE EN PRODUCCIÓN: producción en rev_p_111, copia local en rev_l_222. Se aplica con con_produccion.py antes del push."
    )
    for prohibido in ("FALLO", "ERROR", "DESVIO", "INESPERADA"):
        assert prohibido not in msg_dist
