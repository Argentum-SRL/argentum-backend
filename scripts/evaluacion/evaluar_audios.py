# -*- coding: utf-8 -*-
r"""
scripts/evaluacion/evaluar_audios.py

Evalúa transcripción de audios de D:\Argentum\m2_muestras\audios\ midiendo WER
y detección de números y cuentas, con tope de USD 2 por corrida.
"""
from __future__ import annotations

import argparse
from datetime import datetime
from decimal import Decimal
import json
import math
import os
from pathlib import Path
import re
import sys
import time
from typing import Any
import unicodedata
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

AUDIOS_DIR = Path(r"D:\Argentum\m2_muestras\audios")
TEXTOS_PATH = AUDIOS_DIR / "textos_audios.json"
OUT_DIR = Path(r"D:\Argentum\auditorias\raw\evaluacion_m2")
EXTENSIONES_AUDIO = {".ogg", ".mp3", ".mp4", ".wav", ".webm", ".amr", ".aac", ".m4a", ".opus"}
MIME_AUDIO = {
    ".ogg": "audio/ogg",
    ".opus": "audio/ogg",
    ".mp3": "audio/mpeg",
    ".mp4": "audio/mp4",
    ".m4a": "audio/mp4",
    ".wav": "audio/wav",
    ".webm": "audio/webm",
    ".amr": "audio/amr",
    ".aac": "audio/aac",
}
CUENTAS_CONOCIDAS = (
    "galicia",
    "santander",
    "mercado pago",
    "mercadopago",
    "efectivo",
    "brubank",
    "uala",
    "bbva",
    "frances",
    "lemon",
    "nacion",
    "macro",
    "visa",
    "mastercard",
    "amex",
)
TOPE_CORRIDA_USD = Decimal("2.00")
MENSAJE_TOPE_CORRIDA = "Se cortó: pasó el tope de USD 2 de esta corrida."


def supera_tope_corrida(costo_acumulado: Decimal) -> bool:
    """Devuelve True si el costo acumulado superó el tope de USD 2,00."""
    return Decimal(str(costo_acumulado)) > TOPE_CORRIDA_USD


def _normalizar_palabras(texto: str) -> list[str]:
    sin_tildes = "".join(
        c
        for c in unicodedata.normalize("NFD", (texto or ""))
        if unicodedata.category(c) != "Mn"
    )
    limpio = re.sub(r"[^\w\s]", " ", sin_tildes.lower())
    return limpio.split()


def wer(referencia: str, hipotesis: str) -> float:
    """Calcula el Word Error Rate normalizando mayúsculas, tildes y puntuación."""
    ref = _normalizar_palabras(referencia)
    hyp = _normalizar_palabras(hipotesis)
    if not ref:
        return 0.0 if not hyp else 1.0

    dp = list(range(len(hyp) + 1))
    for i, r_tok in enumerate(ref, start=1):
        nuevo = [i]
        for j, h_tok in enumerate(hyp, start=1):
            costo_sub = dp[j - 1] + (0 if r_tok == h_tok else 1)
            nuevo.append(min(dp[j] + 1, nuevo[j - 1] + 1, costo_sub))
        dp = nuevo
    return dp[-1] / len(ref)


def _limpiar_error(exc: Exception) -> str:
    txt = str(exc)
    if "sk-" in txt:
        return "Error de API (clave oculta)"
    return txt[:300]


def _verificar_numeros_y_cuentas(referencia: str, hipotesis: str) -> dict[str, Any]:
    ref_norm = " ".join(_normalizar_palabras(referencia))
    hyp_norm = " ".join(_normalizar_palabras(hipotesis))
    nums_ref = re.findall(r"\b\d+\b", ref_norm)
    nums_ok = all(n in hyp_norm for n in nums_ref)
    ctas_ref = [c for c in CUENTAS_CONOCIDAS if re.search(rf"\b{re.escape(c)}\b", ref_norm)]
    ctas_ok = all(c in hyp_norm for c in ctas_ref)
    return {
        "numeros_ok": nums_ok,
        "numeros_esperados": nums_ref,
        "cuentas_ok": ctas_ok,
        "cuentas_esperadas": ctas_ref,
    }


def main(argv: list[str] | None = None) -> int:
    db_url = os.environ.get("DATABASE_URL", "")
    if "localhost:5433" not in db_url:
        print("FRENO: falta con_base_local.py")
        return 1

    from app.routers.whatsapp.media import _obtener_duracion_audio_bytes
    from app.services.openai_client import get_openai_client
    from app.services.uso_ia_service import calcular_costo_ia
    from app.utils.fecha import TZ_ARGENTINA

    parser = argparse.ArgumentParser(
        description="Evalúa transcripción de audios entre modelos."
    )
    parser.add_argument("--modelos", default="whisper-1,gpt-transcribe")
    args = parser.parse_args(argv)

    if not AUDIOS_DIR.exists():
        print(f"No existe la carpeta {AUDIOS_DIR}. Sin audios para evaluar.")
        return 0

    audios = sorted(
        p
        for p in AUDIOS_DIR.iterdir()
        if p.is_file() and p.suffix.lower() in EXTENSIONES_AUDIO
    )
    if not audios:
        print(f"No hay archivos de audio en {AUDIOS_DIR}.")
        return 0

    textos_ref: dict[str, str] = {}
    if TEXTOS_PATH.exists():
        textos_ref = json.loads(TEXTOS_PATH.read_text(encoding="utf-8"))

    modelos = [m.strip() for m in args.modelos.split(",") if m.strip()]
    total_llamadas = len(audios) * len(modelos)
    costo_est = sum(
        calcular_costo_ia(m, segundos_audio=60 * len(audios)) for m in modelos
    )
    print(
        f"Llamadas a OpenAI: {total_llamadas} ({len(audios)} audios x "
        f"{len(modelos)} modelos) | Costo máximo estimado: USD {costo_est:.4f}"
    )
    if input("Escribí SI para seguir: ").strip() != "SI":
        print("Cancelado.")
        return 0

    client_oai = get_openai_client()
    costo_acumulado = Decimal("0")
    cortado = False
    resultados: list[dict[str, Any]] = []
    wers_por_modelo: dict[str, list[float]] = {m: [] for m in modelos}

    def _colector_noop(*args, **kwargs):
        return None

    with patch("app.services.uso_ia_service.registrar_uso_ia", side_effect=_colector_noop):
        for audio_path in audios:
            if supera_tope_corrida(costo_acumulado):
                print(MENSAJE_TOPE_CORRIDA)
                cortado = True
                break

            clave = audio_path.stem
            ref_txt = textos_ref.get(clave, "")
            raw_bytes = audio_path.read_bytes()
            mime = MIME_AUDIO.get(audio_path.suffix.lower(), "audio/ogg")
            dur = _obtener_duracion_audio_bytes(raw_bytes, mime)
            segundos = max(1, math.ceil(dur)) if dur is not None else 60

            item_res: dict[str, Any] = {
                "audio": audio_path.name,
                "clave": clave,
                "referencia": ref_txt,
                "modelos": {},
            }
            for mod in modelos:
                if supera_tope_corrida(costo_acumulado):
                    print(MENSAJE_TOPE_CORRIDA)
                    cortado = True
                    break

                t0 = time.perf_counter()
                error_txt = None
                hip_txt = ""
                try:
                    resp = client_oai.audio.transcriptions.create(
                        model=mod,
                        file=(audio_path.name, raw_bytes, mime),
                        language="es",
                    )
                    hip_txt = getattr(resp, "text", "") or ""
                except Exception as exc:
                    error_txt = _limpiar_error(exc)

                dt = time.perf_counter() - t0
                costo_call = calcular_costo_ia(mod, segundos_audio=segundos)
                costo_acumulado += costo_call

                wer_val = wer(ref_txt, hip_txt) if ref_txt and not error_txt else None
                if wer_val is not None:
                    wers_por_modelo[mod].append(wer_val)
                checks = (
                    _verificar_numeros_y_cuentas(ref_txt, hip_txt)
                    if ref_txt and not error_txt
                    else {}
                )
                item_res["modelos"][mod] = {
                    "texto": hip_txt,
                    "wer": wer_val,
                    "checks": checks,
                    "costo_usd": float(costo_call),
                    "tiempo_s": round(dt, 3),
                    "error": error_txt,
                }
            resultados.append(item_res)
            if cortado:
                break

    ahora = datetime.now(TZ_ARGENTINA)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    modelos_tag = "_vs_".join(m.replace("/", "_") for m in modelos)
    base = f"evaluar_audios_{modelos_tag}_none_{ahora:%Y%m%d}_{ahora:%H%M}"
    txt_p = OUT_DIR / f"{base}.txt"
    json_p = OUT_DIR / f"{base}.json"

    lineas = [f"EVALUAR AUDIOS | modelos={modelos}", ""]
    if cortado:
        lineas.append(MENSAJE_TOPE_CORRIDA)
        lineas.append("")
    for r in resultados:
        lineas.append(f"{r['clave']} ({r['audio']}) | ref: {r['referencia']}")
        for mod, info in r["modelos"].items():
            wer_s = f"{info['wer']:.3f}" if info["wer"] is not None else "N/A"
            lineas.append(
                f"  [{mod}] WER={wer_s} | tiempo={info['tiempo_s']}s | "
                f"costo={info['costo_usd']:.6f} | err={info['error']} | "
                f"texto={info['texto']}"
            )
    lineas.append("")
    for mod, vals in wers_por_modelo.items():
        prom = (sum(vals) / len(vals)) if vals else 0.0
        lineas.append(f"WER promedio [{mod}]: {prom:.4f} ({len(vals)} audios)")
    lineas.append(f"Costo total (USD): {costo_acumulado:.6f}")

    txt_p.write_text("\n".join(lineas) + "\n", encoding="utf-8", newline="\n")
    json_p.write_text(
        json.dumps(
            {
                "modelos": modelos,
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
