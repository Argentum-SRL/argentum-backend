# -*- coding: utf-8 -*-
r"""
scripts/evaluacion/evaluar_documentos.py

Evalúa extracción de fotos y PDF de D:\Argentum\m2_muestras\fotos\ comparando
modelos lado a lado, con tope de USD 2 por corrida.
"""
from __future__ import annotations

import argparse
from datetime import datetime
from decimal import Decimal
import json
import os
from pathlib import Path
import sys
import time
from typing import Any
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

MUESTRAS_DIR = Path(r"D:\Argentum\m2_muestras\fotos")
ESPERADO_PATH = Path(r"D:\Argentum\m2_muestras\esperado_documentos.json")
OUT_DIR = Path(r"D:\Argentum\auditorias\raw\evaluacion_m2")
EXTENSIONES_IMG = {".jpg", ".jpeg", ".png", ".webp"}
EXTENSIONES_DOC = EXTENSIONES_IMG | {".pdf"}
MIME_POR_EXT = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
}
TOPE_CORRIDA_USD = Decimal("2.00")
MENSAJE_TOPE_CORRIDA = "Se cortó: pasó el tope de USD 2 de esta corrida."


def supera_tope_corrida(costo_acumulado: Decimal) -> bool:
    """Devuelve True si el costo acumulado superó el tope de USD 2,00."""
    return Decimal(str(costo_acumulado)) > TOPE_CORRIDA_USD


def _limpiar_error(exc: Exception) -> str:
    txt = str(exc)
    if "sk-" in txt:
        return "Error de API (clave oculta)"
    return txt[:300]


def _serializar_movimientos(res: Any) -> list[dict[str, Any]]:
    if res is None:
        return []
    salida = []
    for m in getattr(res, "movimientos", []) or []:
        fecha_val = getattr(m, "fecha", None)
        salida.append(
            {
                "monto": float(m.monto) if m.monto is not None else None,
                "moneda": m.moneda,
                "fecha": fecha_val.isoformat() if fecha_val else None,
                "comercio": getattr(m, "descripcion", None),
                "categoria": getattr(m, "categoria", None),
                "tipo": getattr(m, "sentido", None),
            }
        )
    return salida


def _comparar_contra_esperado(
    esperados: list[dict[str, Any]],
    obtenidos: list[dict[str, Any]],
) -> dict[str, tuple[int, int]]:
    conteo: dict[str, list[int]] = {
        "monto": [0, 0],
        "moneda": [0, 0],
        "fecha": [0, 0],
        "tipo": [0, 0],
    }
    for idx, esp in enumerate(esperados):
        obt = obtenidos[idx] if idx < len(obtenidos) else {}
        for campo in ("monto", "moneda", "fecha", "tipo"):
            if campo not in esp:
                continue
            conteo[campo][1] += 1
            if campo == "monto":
                try:
                    ok = obt.get("monto") is not None and (
                        Decimal(str(esp["monto"])) == Decimal(str(obt["monto"]))
                    )
                except Exception:
                    ok = False
            else:
                ok = str(esp[campo]).strip().lower() == str(
                    obt.get(campo) or ""
                ).strip().lower()
            if ok:
                conteo[campo][0] += 1
    return {k: (v[0], v[1]) for k, v in conteo.items()}


def main(argv: list[str] | None = None) -> int:
    db_url = os.environ.get("DATABASE_URL", "")
    if "localhost:5433" not in db_url:
        print("FRENO: falta con_base_local.py")
        return 1

    from sqlalchemy import select
    from app.core.config import settings
    from app.core.database import SessionLocal
    from app.models.usuario import Usuario
    from app.routers.whatsapp.extraccion_documento import (
        extraer_movimientos_de_imagen,
        extraer_movimientos_de_texto_pdf,
    )
    from app.routers.whatsapp.pdf_documento import leer_texto_pdf
    from app.services.ai_service import obtener_categorias_permitidas
    from app.services.uso_ia_service import (
        _extraer_tokens_de_respuesta,
        calcular_costo_ia,
    )
    from app.utils.fecha import TZ_ARGENTINA

    parser = argparse.ArgumentParser(
        description="Evalúa lectura de fotos y PDF entre modelos."
    )
    parser.add_argument("--modelos", default="gpt-4o,gpt-6-luna")
    parser.add_argument(
        "--razonamiento",
        default=settings.OPENAI_RAZONAMIENTO,
        choices=["none", "low", "medium"],
    )
    args = parser.parse_args(argv)

    if not MUESTRAS_DIR.exists():
        print(f"No existe la carpeta {MUESTRAS_DIR}. Sin muestras para evaluar.")
        return 0

    archivos = sorted(
        p
        for p in MUESTRAS_DIR.iterdir()
        if p.is_file() and p.suffix.lower() in EXTENSIONES_DOC
    )
    if not archivos:
        print(f"No hay archivos .jpg/.jpeg/.png/.webp/.pdf en {MUESTRAS_DIR}.")
        return 0

    modelos = [m.strip() for m in args.modelos.split(",") if m.strip()]
    total_llamadas = len(archivos) * len(modelos)
    costo_est = sum(
        calcular_costo_ia(m, 3500 * len(archivos), 0, 500 * len(archivos))
        for m in modelos
    )
    print(
        f"Llamadas a OpenAI: {total_llamadas} ({len(archivos)} archivos x "
        f"{len(modelos)} modelos) | Costo máximo estimado: USD {costo_est:.4f}"
    )
    if input("Escribí SI para seguir: ").strip() != "SI":
        print("Cancelado.")
        return 0

    esperado_map: dict[str, list[dict[str, Any]]] = {}
    if ESPERADO_PATH.exists():
        esperado_map = json.loads(ESPERADO_PATH.read_text(encoding="utf-8"))

    db = SessionLocal()
    try:
        u = db.execute(
            select(Usuario).where(Usuario.email == "testingadmin@argentum.com")
        ).scalar_one()
        nombre_usuario = f"{u.nombre or ''} {u.apellido or ''}".strip()
        categorias_usuario = obtener_categorias_permitidas(db)
        usuario_id = u.id
    finally:
        db.close()

    settings.OPENAI_RAZONAMIENTO = args.razonamiento
    costo_acumulado = Decimal("0")
    cortado = False
    estado_llamada = {"costo": Decimal("0")}

    def _colector_uso(usuario_id, funcion, modelo, respuesta=None, segundos_audio=0):
        t_in, t_cache, t_out = _extraer_tokens_de_respuesta(respuesta)
        estado_llamada["costo"] += calcular_costo_ia(
            modelo=modelo,
            tokens_entrada=t_in,
            tokens_entrada_cache=t_cache,
            tokens_salida=t_out,
            segundos_audio=segundos_audio,
        )

    resultados: list[dict[str, Any]] = []
    aciertos_por_modelo: dict[str, dict[str, list[int]]] = {
        m: {c: [0, 0] for c in ("monto", "moneda", "fecha", "tipo")} for m in modelos
    }

    with patch("app.services.uso_ia_service.registrar_uso_ia", side_effect=_colector_uso):
        for archivo in archivos:
            if supera_tope_corrida(costo_acumulado):
                print(MENSAJE_TOPE_CORRIDA)
                cortado = True
                break

            ext = archivo.suffix.lower()
            raw_bytes = archivo.read_bytes()
            texto_pdf = None
            err_pdf = None
            if ext == ".pdf":
                texto_pdf, err_pdf = leer_texto_pdf(raw_bytes, max_paginas=6)

            item_res: dict[str, Any] = {"archivo": archivo.name, "modelos": {}}
            for mod in modelos:
                if supera_tope_corrida(costo_acumulado):
                    print(MENSAJE_TOPE_CORRIDA)
                    cortado = True
                    break

                settings.OPENAI_MODEL_DOCUMENTOS = mod
                estado_llamada["costo"] = Decimal("0")
                t0 = time.perf_counter()
                error_txt = None
                movs: list[dict[str, Any]] = []

                try:
                    if ext == ".pdf":
                        if err_pdf or not texto_pdf:
                            error_txt = f"PDF_ERROR: {err_pdf or 'SIN_TEXTO'}"
                        else:
                            res_ext, err_ext = extraer_movimientos_de_texto_pdf(
                                texto=texto_pdf,
                                nombre_usuario=nombre_usuario,
                                categorias_usuario=categorias_usuario,
                                usuario_id=usuario_id,
                            )
                            if err_ext:
                                error_txt = err_ext
                            movs = _serializar_movimientos(res_ext)
                    else:
                        mime = MIME_POR_EXT.get(ext, "image/jpeg")
                        res_ext, err_ext = extraer_movimientos_de_imagen(
                            image_bytes=raw_bytes,
                            mime_type=mime,
                            nombre_usuario=nombre_usuario,
                            categorias_usuario=categorias_usuario,
                            usuario_id=usuario_id,
                        )
                        if err_ext:
                            error_txt = err_ext
                        movs = _serializar_movimientos(res_ext)
                except Exception as exc:
                    error_txt = _limpiar_error(exc)

                dt = time.perf_counter() - t0
                costo_llamada = estado_llamada["costo"]
                costo_acumulado += costo_llamada

                if archivo.name in esperado_map:
                    comp = _comparar_contra_esperado(esperado_map[archivo.name], movs)
                    for campo, (ok_n, tot_n) in comp.items():
                        aciertos_por_modelo[mod][campo][0] += ok_n
                        aciertos_por_modelo[mod][campo][1] += tot_n

                item_res["modelos"][mod] = {
                    "movimientos": movs,
                    "costo_usd": float(costo_llamada),
                    "tiempo_s": round(dt, 3),
                    "error": error_txt,
                }
            resultados.append(item_res)
            if cortado:
                break

    ahora = datetime.now(TZ_ARGENTINA)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    modelos_tag = "_vs_".join(m.replace("/", "_") for m in modelos)
    base = (
        f"evaluar_documentos_{modelos_tag}_{args.razonamiento}_"
        f"{ahora:%Y%m%d}_{ahora:%H%M}"
    )
    txt_p = OUT_DIR / f"{base}.txt"
    json_p = OUT_DIR / f"{base}.json"

    lineas = [
        f"EVALUAR DOCUMENTOS | modelos={modelos} | razonamiento={args.razonamiento}",
        "",
    ]
    if cortado:
        lineas.append(MENSAJE_TOPE_CORRIDA)
        lineas.append("")
    for r in resultados:
        lineas.append(f"Archivo: {r['archivo']}")
        for mod, info in r["modelos"].items():
            lineas.append(
                f"  [{mod}] tiempo={info['tiempo_s']}s | "
                f"costo={info['costo_usd']:.6f} | err={info['error']}"
            )
            for m in info["movimientos"]:
                lineas.append(
                    f"    - {m['tipo']} | {m['monto']} {m['moneda']} | "
                    f"{m['fecha']} | {m['comercio']} | {m['categoria']}"
                )
    if esperado_map:
        lineas.append("")
        lineas.append("Comparación contra esperado_documentos.json:")
        for mod, campos in aciertos_por_modelo.items():
            res_c = ", ".join(f"{c}: {v[0]}/{v[1]}" for c, v in campos.items())
            lineas.append(f"  [{mod}] {res_c}")
    lineas.append(f"\nCosto total (USD): {costo_acumulado:.6f}")

    txt_p.write_text("\n".join(lineas) + "\n", encoding="utf-8", newline="\n")
    json_p.write_text(
        json.dumps(
            {
                "modelos": modelos,
                "razonamiento": args.razonamiento,
                "cortado": cortado,
                "costo_total_usd": float(costo_acumulado),
                "resultados": resultados,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
        newline="\n",
    )
    print(f"Escrito: {txt_p}")
    print(f"Escrito: {json_p}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
