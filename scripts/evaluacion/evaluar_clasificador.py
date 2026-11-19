# -*- coding: utf-8 -*-
"""
scripts/evaluacion/evaluar_clasificador.py

Evalúa el clasificador de conceptos contra las 69 frases con respuesta esperada.
Se corre con:
  venv\\Scripts\\python.exe scripts\\local\\con_base_local.py \\
    venv\\Scripts\\python.exe scripts\\evaluacion\\evaluar_clasificador.py
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
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

FRASES_PATH = Path(__file__).resolve().parent / "frases_clasificador.json"
OUT_DIR = Path(r"D:\Argentum\auditorias\raw\evaluacion_m2")
TOPE_CORRIDA_USD = Decimal("2.00")
MENSAJE_TOPE_CORRIDA = "Se cortó: pasó el tope de USD 2 de esta corrida."


def supera_tope_corrida(costo_acumulado: Decimal) -> bool:
    """Devuelve True si el costo acumulado superó el tope de USD 2,00."""
    return Decimal(str(costo_acumulado)) > TOPE_CORRIDA_USD


def _formatear_par(par: tuple[str, str | None]) -> str:
    cat, sub = par
    return f"{cat} \u203a {sub or 'Otros'}"


def _limpiar_error(exc: Exception) -> str:
    txt = str(exc)
    if "sk-" in txt:
        return "Error de API (clave oculta)"
    return txt[:300]


def main(argv: list[str] | None = None) -> int:
    db_url = os.environ.get("DATABASE_URL", "")
    if "localhost:5433" not in db_url:
        print("FRENO: falta con_base_local.py")
        return 1

    from app.core.config import settings
    from app.core.database import SessionLocal
    from app.services import clasificador_concepto_service
    from app.services.uso_ia_service import (
        _extraer_tokens_de_respuesta,
        calcular_costo_ia,
    )
    from app.utils.fecha import TZ_ARGENTINA

    parser = argparse.ArgumentParser(
        description="Evalúa el clasificador de conceptos contra 69 frases."
    )
    parser.add_argument("--modelo", default=settings.OPENAI_MODEL)
    parser.add_argument(
        "--razonamiento",
        default=settings.OPENAI_RAZONAMIENTO,
        choices=["none", "low", "medium"],
    )
    args = parser.parse_args(argv)

    frases = json.loads(FRASES_PATH.read_text(encoding="utf-8"))
    total = len(frases)
    costo_est = calcular_costo_ia(args.modelo, 1800 * total, 0, 50 * total)
    print(
        f"Llamadas a OpenAI: {total} | "
        f"Costo máximo estimado: USD {costo_est:.4f}"
    )
    confirmacion = input("Escribí SI para seguir: ")
    if confirmacion.strip() != "SI":
        print("Cancelado.")
        return 0

    settings.OPENAI_MODEL = args.modelo
    settings.OPENAI_RAZONAMIENTO = args.razonamiento
    clasificador_concepto_service.limpiar_caches_clasificador()

    costo_acumulado = Decimal("0")
    cortado = False
    tiempos_s: list[float] = []
    detalles_json: list[dict] = []

    orig_llamar = clasificador_concepto_service.llamar_clasificador

    def _llamar_registrando(
        mensajes,
        schema,
        model=None,
        temperature=0,
        max_tokens=120,
    ):
        nonlocal costo_acumulado
        t0 = time.perf_counter()
        try:
            resp = orig_llamar(
                mensajes,
                schema,
                model=model,
                temperature=temperature,
                max_tokens=max_tokens,
            )
        finally:
            tiempos_s.append(time.perf_counter() - t0)
        t_in, t_cache, t_out = _extraer_tokens_de_respuesta(resp)
        costo_acumulado += calcular_costo_ia(
            args.modelo,
            tokens_entrada=t_in,
            tokens_entrada_cache=t_cache,
            tokens_salida=t_out,
        )
        return resp

    clasificador_concepto_service.llamar_clasificador = _llamar_registrando
    db = SessionLocal()
    aciertos_1 = 0
    aciertos_3 = 0
    lineas: list[str] = [
        f"EVALUAR CLASIFICADOR | modelo={args.modelo} | "
        f"razonamiento={args.razonamiento}",
        "",
    ]

    def _colector_noop(*a, **k):
        return None

    try:
        with patch("app.services.uso_ia_service.registrar_uso_ia", side_effect=_colector_noop):
            for idx, item in enumerate(frases, start=1):
                if supera_tope_corrida(costo_acumulado):
                    print(MENSAJE_TOPE_CORRIDA)
                    lineas.append(MENSAJE_TOPE_CORRIDA)
                    cortado = True
                    break
                frase = item["frase"]
                tipo = item["tipo"]
                esperado = item["esperado"]
                err_txt = None
                try:
                    pares = clasificador_concepto_service.clasificar_sin_limites(
                        db, frase, tipo
                    )
                except Exception as exc:
                    err_txt = _limpiar_error(exc)
                    opciones_str = [f"ERROR_LLAMADA: {err_txt}"]
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

                t_call = tiempos_s[-1] if tiempos_s else 0.0
                lineas.append(
                    f"{idx:02d}. [{tipo}] {frase} | esperado={esperado} | "
                    f"dio={opciones_str[:3]} | {estado} | {t_call:.3f}s"
                )
                detalles_json.append(
                    {
                        "idx": idx,
                        "tipo": tipo,
                        "frase": frase,
                        "esperado": esperado,
                        "dio": opciones_str[:3],
                        "estado": estado,
                        "tiempo_s": round(t_call, 3),
                        "error": err_txt,
                    }
                )
                if supera_tope_corrida(costo_acumulado):
                    print(MENSAJE_TOPE_CORRIDA)
                    lineas.append(MENSAJE_TOPE_CORRIDA)
                    cortado = True
                    break
    finally:
        clasificador_concepto_service.llamar_clasificador = orig_llamar
        db.rollback()
        db.close()

    eval_n = len(detalles_json)
    pct_1 = (aciertos_1 * 100.0 / eval_n) if eval_n else 0.0
    pct_3 = (aciertos_3 * 100.0 / eval_n) if eval_n else 0.0
    lineas.extend(
        [
            "",
            f"Primera opción: {aciertos_1}/{eval_n} ({pct_1:.1f}%)",
            f"Entre las 3: {aciertos_3}/{eval_n} ({pct_3:.1f}%)",
            f"Modelo: {args.modelo} | Razonamiento: {args.razonamiento}",
            f"Costo estimado (USD): {costo_acumulado:.6f}",
        ]
    )

    ahora = datetime.now(TZ_ARGENTINA)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    mod_safe = args.modelo.replace("/", "_").replace(":", "_")
    base = (
        f"evaluar_clasificador_{mod_safe}_{args.razonamiento}_"
        f"{ahora:%Y%m%d}_{ahora:%H%M}"
    )
    txt_p = OUT_DIR / f"{base}.txt"
    json_p = OUT_DIR / f"{base}.json"
    txt_p.write_text("\n".join(lineas) + "\n", encoding="utf-8", newline="\n")
    json_p.write_text(
        json.dumps(
            {
                "modelo": args.modelo,
                "razonamiento": args.razonamiento,
                "cortado": cortado,
                "aciertos_1": aciertos_1,
                "aciertos_3": aciertos_3,
                "evaluadas": eval_n,
                "costo_total_usd": float(costo_acumulado),
                "resultados": detalles_json,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
        newline="\n",
    )
    print(f"Escrito: {txt_p}")
    print(f"Escrito: {json_p}")
    print(f"Primera opción: {aciertos_1}/{eval_n} ({pct_1:.1f}%)")
    print(f"Entre las 3: {aciertos_3}/{eval_n} ({pct_3:.1f}%)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
