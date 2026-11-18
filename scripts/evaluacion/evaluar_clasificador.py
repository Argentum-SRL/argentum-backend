# -*- coding: utf-8 -*-
"""
scripts/evaluacion/evaluar_clasificador.py

Evalúa el clasificador de conceptos contra las 69 frases con respuesta esperada.
Se corre con:
  venv\\Scripts\\python.exe scripts\\local\\con_base_local.py \\
    venv\\Scripts\\python.exe scripts\\evaluacion\\evaluar_clasificador.py
"""
from __future__ import annotations

from decimal import Decimal
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core.config import settings  # noqa: E402
from app.core.database import SessionLocal  # noqa: E402
from app.services import clasificador_concepto_service  # noqa: E402
from app.services.uso_ia_service import calcular_costo_ia  # noqa: E402
from app.utils.fecha import TZ_ARGENTINA  # noqa: E402
from datetime import datetime  # noqa: E402

FRASES_PATH = Path(__file__).resolve().parent / "frases_clasificador.json"
OUT_DIR = Path(r"D:\Argentum\auditorias\raw\evaluacion_clasificador")


def _formatear_par(par: tuple[str, str | None]) -> str:
    cat, sub = par
    return f"{cat} \u203a {sub or 'Otros'}"


def main() -> int:
    db_url = os.environ.get("DATABASE_URL", "")
    if "localhost:5433" not in db_url:
        print("FRENO: falta con_base_local.py")
        return 1

    confirmacion = input(
        "Esto llama a OpenAI 69 veces (menos de USD 0,10). "
        "Escrib\u00ed SI para seguir: "
    )
    if confirmacion.strip() != "SI":
        print("Cancelado.")
        return 0

    frases = json.loads(FRASES_PATH.read_text(encoding="utf-8"))
    total = len(frases)
    usages: list[object] = []

    orig_llamar = clasificador_concepto_service.llamar_clasificador

    def _llamar_registrando(
        mensajes,
        schema,
        model=None,
        temperature=0,
        max_tokens=120,
    ):
        resp = orig_llamar(
            mensajes,
            schema,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        usages.append(resp.usage)
        return resp

    clasificador_concepto_service.llamar_clasificador = _llamar_registrando

    db = SessionLocal()
    aciertos_1 = 0
    aciertos_3 = 0
    lineas: list[str] = []

    try:
        for idx, item in enumerate(frases, start=1):
            frase = item["frase"]
            tipo = item["tipo"]
            esperado = item["esperado"]
            try:
                pares = clasificador_concepto_service.clasificar_sin_limites(
                    db, frase, tipo
                )
            except Exception as exc:
                opciones_str = [f"ERROR_LLAMADA: {exc}"]
            else:
                opciones_str = [_formatear_par(p) for p in pares]

            if opciones_str and opciones_str[0] == esperado:
                estado = "ACIERTO_1"
                aciertos_1 += 1
                aciertos_3 += 1
            elif esperado in opciones_str[:3]:
                estado = "ACIERTO_3"
                aciertos_3 += 1
            else:
                estado = "ERROR"

            lineas.append(
                f"{idx:02d}. [{tipo}] {frase} | esperado={esperado} | "
                f"dio={opciones_str[:3]} | {estado}"
            )
    finally:
        clasificador_concepto_service.llamar_clasificador = orig_llamar
        db.rollback()
        db.close()

    costo_total = Decimal("0")
    for u in usages:
        if u is None:
            continue
        t_in = int(u.prompt_tokens or 0)
        t_out = int(u.completion_tokens or 0)
        det = u.prompt_tokens_details
        t_cache = int(det.cached_tokens or 0) if det else 0
        costo_total += calcular_costo_ia(
            settings.OPENAI_MODEL,
            tokens_entrada=t_in,
            tokens_entrada_cache=t_cache,
            tokens_salida=t_out,
        )

    pct_1 = (aciertos_1 * 100.0 / total) if total else 0.0
    pct_3 = (aciertos_3 * 100.0 / total) if total else 0.0
    lineas.extend(
        [
            "",
            f"Primera opci\u00f3n: {aciertos_1}/{total} ({pct_1:.1f}%)",
            f"Entre las 3: {aciertos_3}/{total} ({pct_3:.1f}%)",
            f"Modelo: {settings.OPENAI_MODEL}",
            f"Costo estimado (USD): {costo_total:.6f}",
        ]
    )

    ahora = datetime.now(TZ_ARGENTINA)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    archivo = OUT_DIR / f"evaluacion_{ahora:%Y%m%d}_{ahora:%H%M%S}.txt"
    archivo.write_text("\n".join(lineas) + "\n", encoding="utf-8", newline="\n")
    print(f"Escrito: {archivo}")
    print(f"Primera opci\u00f3n: {aciertos_1}/{total} ({pct_1:.1f}%)")
    print(f"Entre las 3: {aciertos_3}/{total} ({pct_3:.1f}%)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
