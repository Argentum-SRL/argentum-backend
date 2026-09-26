"""
Herramienta de Auditoría y Medición: Foto del Motor de Argentum.

Propósito:
Registra una foto determinística de todos los valores calculados actualmente
por el backend de Argentum para el usuario testingadmin@argentum.com.
Permite establecer la 'vara de medir' cuantitativa antes de realizar cualquier
refactorización o rediseño del motor financiero.

Red de seguridad:
- Solo acepta testingadmin@argentum.com (id: 4c2ed62e-c22e-4d21-9bf9-ec6705f2c6fa).
- Abre una sesión exclusiva con listener de evento 'before_commit' que lanza
  RuntimeError ante cualquier intento de commit, impidiendo escrituras en base de datos.
- Cierra siempre con rollback() en bloque finally.
- Cada bloque de medición (A a P) se ejecuta en un try/except independiente.
- Las salidas se guardan en JSON plano y TXT en la carpeta auditorias/fotos/.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import date, datetime, timedelta, timezone
from typing import Any

# Asegurar path de importación del backend
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from sqlalchemy import event
from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.models.usuario import Usuario
from app.services import dashboard_service
from app.utils.fecha import hoy_argentina
from scripts.motor.mediciones import (
    medir_bloque_b,
    medir_bloque_c,
    medir_bloque_d,
    medir_bloque_e,
    medir_bloque_f,
    medir_bloque_g,
    medir_bloque_h,
    medir_bloque_i,
    medir_bloque_j,
    medir_bloque_k,
    medir_bloque_l,
    medir_bloque_m,
    medir_bloque_n,
    medir_bloque_o,
    medir_bloque_p,
)

TESTINGADMIN_EMAIL = "testingadmin@argentum.com"
TESTINGADMIN_ID = "4c2ed62e-c22e-4d21-9bf9-ec6705f2c6fa"


def _obtener_git_hash(repo_dir: str) -> tuple[str, str]:
    """Obtiene el hash del commit actual y la rama del repositorio indicado."""
    try:
        commit = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_dir,
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
        branch = subprocess.check_output(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=repo_dir,
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
        return commit, branch
    except Exception as e:
        return f"ERROR_GIT: {str(e)}", "desconocida"


def crear_sesion_segura() -> Session:
    """
    Crea una sesión de base de datos con interceptor before_commit.
    Cualquier intento de commit lanzará RuntimeError, impidiendo escrituras.
    """
    db = SessionLocal()

    @event.listens_for(db, "before_commit")
    def _bloquear_commit(session: Session) -> None:
        raise RuntimeError(
            "BLOQUEO DE SEGURIDAD: Intento de commit interceptado y cancelado en sesión de foto_motor."
        )

    return db


def medir_bloque_a(
    usuario: Usuario,
    hoy: date,
    backend_dir: str,
    frontend_dir: str,
    etiqueta: str,
    ts_now: datetime,
) -> dict[str, str]:
    """Bloque A: Datos de la foto, repositorios y reglas del ciclo."""
    res = {}
    res["foto.fecha_hora_iso"] = ts_now.isoformat()
    res["foto.fecha_hora_legible"] = ts_now.strftime("%Y-%m-%d %H:%M:%S")
    res["foto.etiqueta"] = etiqueta
    res["foto.fecha_referencia_hoy"] = hoy.isoformat()

    back_hash, back_branch = _obtener_git_hash(backend_dir)
    front_hash, front_branch = _obtener_git_hash(frontend_dir)
    res["repositorio.backend.commit"] = back_hash
    res["repositorio.backend.branch"] = back_branch
    res["repositorio.frontend.commit"] = front_hash
    res["repositorio.frontend.branch"] = front_branch

    res["usuario.id"] = str(usuario.id)
    res["usuario.email"] = str(usuario.email)
    res["usuario.ciclo_tipo"] = (
        usuario.ciclo_tipo.value if usuario.ciclo_tipo else "null"
    )
    res["usuario.ciclo_valor"] = str(usuario.ciclo_valor)
    res["usuario.ciclo_ajuste_direccion"] = (
        usuario.ciclo_ajuste_direccion.value
        if usuario.ciclo_ajuste_direccion
        else "null"
    )

    ini_act, fin_act = dashboard_service.get_ciclo_fechas(usuario, hoy)
    ini_sig, fin_sig = dashboard_service.get_ciclo_fechas(
        usuario, fin_act + timedelta(days=1)
    )
    res["ciclo.actual.inicio"] = ini_act.isoformat()
    res["ciclo.actual.fin"] = fin_act.isoformat()
    res["ciclo.actual.dias_totales"] = str((fin_act - ini_act).days + 1)
    res["ciclo.siguiente.inicio"] = ini_sig.isoformat()
    res["ciclo.siguiente.fin"] = fin_sig.isoformat()
    res["ciclo.siguiente.dias_totales"] = str((fin_sig - ini_sig).days + 1)
    return res


def ejecutar_foto_motor(
    etiqueta: str,
    probar_bloqueo: bool = False,
    output_dir: str | None = None,
) -> tuple[dict[str, str], str, str]:
    """
    Ejecuta la toma integral de la foto del motor financiero para testingadmin.
    Devuelve (foto_dict, ruta_json, ruta_txt).
    """
    backend_dir = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "..")
    )
    frontend_dir = os.path.abspath(os.path.join(backend_dir, "..", "argentum-frontend"))
    if output_dir is None:
        output_dir = os.path.abspath(
            os.path.join(backend_dir, "..", "auditorias", "fotos")
        )
    os.makedirs(output_dir, exist_ok=True)

    db = crear_sesion_segura()
    try:
        # Verificación estricta del usuario canario
        usuario = (
            db.query(Usuario)
            .filter(Usuario.email == TESTINGADMIN_EMAIL)
            .first()
        )
        if not usuario:
            raise ValueError(f"Usuario {TESTINGADMIN_EMAIL} no existe en la base de datos.")
        if str(usuario.id) != TESTINGADMIN_ID:
            raise ValueError(
                f"ID de testingadmin no coincide. Esperado {TESTINGADMIN_ID}, obtenido {usuario.id}"
            )

        # Prueba de red de seguridad ante commit
        if probar_bloqueo:
            print("EJECUTANDO PRUEBA DE BLOQUEO DE COMMIT...")
            try:
                db.commit()
                print("FALLO: La red de seguridad NO bloqueó el commit.")
                sys.exit(1)
            except RuntimeError as e:
                print(f"CORRECTO: Commit interceptado y bloqueado con éxito: {e}")
                db.rollback()

        ts_now = datetime.now(timezone.utc)
        hoy = hoy_argentina()
        ini_act, fin_act = dashboard_service.get_ciclo_fechas(usuario, hoy)
        ini_sig, fin_sig = dashboard_service.get_ciclo_fechas(
            usuario, fin_act + timedelta(days=1)
        )

        timestamp_str = ts_now.strftime("%Y%m%d_%H%M")
        nombre_base = f"foto_{timestamp_str}_{etiqueta}"
        json_path = os.path.join(output_dir, f"{nombre_base}.json")
        txt_path = os.path.join(output_dir, f"{nombre_base}.txt")

        foto: dict[str, str] = {}
        foto["foto.nombre_archivo"] = nombre_base

        # Ejecución independiente y protegida de cada bloque
        bloques = [
            ("A_datos_foto", lambda: medir_bloque_a(usuario, hoy, backend_dir, frontend_dir, etiqueta, ts_now)),
            ("B_billeteras", lambda: medir_bloque_b(db, usuario)),
            ("C_saldo_disponible", lambda: medir_bloque_c(db, usuario)),
            ("D_gasto_ciclo", lambda: medir_bloque_d(db, usuario, hoy, ini_act, fin_act)),
            ("E_ingreso", lambda: medir_bloque_e(db, usuario, ini_act, fin_act)),
            ("F_perfil", lambda: medir_bloque_f(db, usuario)),
            ("G_proyeccion", lambda: medir_bloque_g(db, usuario)),
            ("H_clasificacion", lambda: medir_bloque_h(db, usuario)),
            ("I_puede_permitirse", lambda: medir_bloque_i(db, usuario)),
            ("J_cuotas_vs_contado", medir_bloque_j),
            ("K_whatsapp_textos", lambda: medir_bloque_k(db, usuario, hoy, (ini_act, fin_act))),
            ("L_contexto_ia", lambda: medir_bloque_l(db, usuario)),
            ("M_suscripciones", lambda: medir_bloque_m(db, usuario)),
            ("N_cuotas", lambda: medir_bloque_n(db, usuario, hoy, ini_act, fin_act, ini_sig, fin_sig)),
            ("O_presupuestos_metas", lambda: medir_bloque_o(db, usuario)),
            ("P_ipc_dolar", lambda: medir_bloque_p(db)),
        ]

        for tag_bloque, func_bloque in bloques:
            try:
                datos_bloque = func_bloque()
                foto.update(datos_bloque)
            except Exception as e:
                foto[f"error_bloque.{tag_bloque}"] = f"ERROR: {type(e).__name__}: {str(e)}"

        # Asignar rutas finales al metadata
        foto["foto.archivo_json"] = json_path
        foto["foto.archivo_txt"] = txt_path

        # Guardar JSON con orden determinístico de claves
        with open(json_path, "w", encoding="utf-8") as f_json:
            json.dump(foto, f_json, indent=2, ensure_ascii=False, sort_keys=True)

        # Guardar TXT legible
        with open(txt_path, "w", encoding="utf-8") as f_txt:
            f_txt.write("=== FOTO DEL MOTOR DE ARGENTUM ===\n")
            f_txt.write(f"Etiqueta: {etiqueta}\n")
            f_txt.write(f"Fecha: {foto.get('foto.fecha_hora_legible')}\n")
            f_txt.write(f"Total claves registradas: {len(foto)}\n")
            f_txt.write("=" * 80 + "\n\n")
            for k in sorted(foto.keys()):
                val = foto[k]
                if "\n" in val:
                    f_txt.write(f"{k}:\n")
                    for line in val.splitlines():
                        f_txt.write(f"    {line}\n")
                else:
                    f_txt.write(f"{k} = {val}\n")

        return foto, json_path, txt_path

    finally:
        db.rollback()
        db.close()


def main():
    parser = argparse.ArgumentParser(
        description="Generador de foto determinística del motor financiero de Argentum."
    )
    parser.add_argument(
        "--etiqueta",
        type=str,
        default="actual",
        help="Etiqueta identificadora de la foto (ej: actual_1, actual_2, post_cambio)",
    )
    parser.add_argument(
        "--probar-bloqueo",
        action="store_true",
        help="Ejecuta una prueba explícita de bloqueo de commit para validar la red de seguridad.",
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default=None,
        help="Carpeta destino donde guardar la foto JSON y TXT.",
    )
    args = parser.parse_args()

    try:
        foto, json_path, txt_path = ejecutar_foto_motor(
            etiqueta=args.etiqueta,
            probar_bloqueo=args.probar_bloqueo,
            output_dir=args.output_dir,
        )
        print("FOTO DEL MOTOR COMPLETADA EXITOSAMENTE")
        print(f"Total de métricas y claves capturadas: {len(foto)}")
        print(f"Archivo JSON: {json_path}")
        print(f"Archivo TXT:  {txt_path}")
    except Exception as e:
        print(f"ERROR FATAL AL EJECUTAR FOTO_MOTOR: {type(e).__name__}: {str(e)}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
