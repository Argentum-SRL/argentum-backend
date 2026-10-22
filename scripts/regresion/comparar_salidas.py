"""
Comparador de fotos de salida de la suite de regresión de WhatsApp.
Compara a.json y b.json escenario por escenario:
- Mensajes enviados por el bot (orden y texto)
- Movimientos creados antes del rollback
- Filas de conversaciones_wpp creadas (intent y accion_ejecutada)
Imprime IGUAL por escenario, o el detalle de cada diferencia.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
import re


def _normalizar_mensaje(msg: str) -> str:
    # Sin horas de creación para que dos corridas del mismo día sean comparables (Decisión A)
    return re.sub(r"\b\d{1,2}:\d{2}\b", "<HORA>", msg)


def serializar_item(item: dict) -> str:
    return json.dumps(item, ensure_ascii=False, sort_keys=True)


def comparar_escenario(eid: str, sal_a: dict, sal_b: dict, unir_mensajes: bool = False) -> list[str]:
    diferencias = []

    # 1. Comparar mensajes
    if unir_mensajes:
        msgs_a_raw = sal_a.get("mensajes", [])
        msgs_b_raw = sal_b.get("mensajes", [])
        msg_a_unido = "\n\n".join([_normalizar_mensaje(m) for m in msgs_a_raw])
        msg_b_unido = "\n\n".join([_normalizar_mensaje(m) for m in msgs_b_raw])
        if msg_a_unido != msg_b_unido:
            diferencias.append("  MENSAJES (unidos):")
            diferencias.append(f"    A: {msg_a_unido}")
            diferencias.append(f"    B: {msg_b_unido}")
    else:
        msgs_a = [_normalizar_mensaje(m) for m in sal_a.get("mensajes", [])]
        msgs_b = [_normalizar_mensaje(m) for m in sal_b.get("mensajes", [])]
        if msgs_a != msgs_b:
            diferencias.append(f"  MENSAJES ({len(msgs_a)} vs {len(msgs_b)}):")
            diferencias.append(f"    A: {msgs_a}")
            diferencias.append(f"    B: {msgs_b}")

    # 2. Comparar movimientos
    movs_a = sal_a.get("movimientos", [])
    movs_b = sal_b.get("movimientos", [])
    if movs_a != movs_b:
        diferencias.append(f"  MOVIMIENTOS ({len(movs_a)} vs {len(movs_b)}):")
        diferencias.append(f"    A: {json.dumps(movs_a, ensure_ascii=False, indent=6)}")
        diferencias.append(f"    B: {json.dumps(movs_b, ensure_ascii=False, indent=6)}")

    # 3. Comparar conversaciones
    convs_a = sal_a.get("conversaciones", [])
    convs_b = sal_b.get("conversaciones", [])
    if convs_a != convs_b:
        diferencias.append(f"  CONVERSACIONES ({len(convs_a)} vs {len(convs_b)}):")
        diferencias.append(f"    A: {json.dumps(convs_a, ensure_ascii=False, indent=6)}")
        diferencias.append(f"    B: {json.dumps(convs_b, ensure_ascii=False, indent=6)}")

    return diferencias


def main():
    parser = argparse.ArgumentParser(description="Comparador de fotos de salida de la suite de WhatsApp")
    parser.add_argument("salidas_a", type=str, help="Ruta de salidas A")
    parser.add_argument("salidas_b", type=str, help="Ruta de salidas B")
    parser.add_argument("--unir-mensajes", action="store_true", help="Compara mensajes unidos con \\n\\n")
    args = parser.parse_args()

    path_a = Path(args.salidas_a)
    path_b = Path(args.salidas_b)

    if not path_a.exists():
        print(f"ERROR: No existe archivo {path_a}")
        sys.exit(2)
    if not path_b.exists():
        print(f"ERROR: No existe archivo {path_b}")
        sys.exit(2)

    with open(path_a, "r", encoding="utf-8") as f:
        data_a: dict = json.load(f)

    with open(path_b, "r", encoding="utf-8") as f:
        data_b: dict = json.load(f)

    todos_los_ids = sorted(list(set(data_a.keys()) | set(data_b.keys())))

    iguales = 0
    distintos = 0
    faltantes = 0

    print("=== COMPARACION DE SALIDAS DE LA SUITE ===")
    print(f"Archivo A: {path_a}")
    print(f"Archivo B: {path_b}")
    print(f"Total escenarios a comparar: {len(todos_los_ids)}\n")

    for eid in todos_los_ids:
        if eid not in data_a:
            print(f"[{eid}] FALTANTE en A")
            faltantes += 1
            distintos += 1
            continue
        if eid not in data_b:
            print(f"[{eid}] FALTANTE en B")
            faltantes += 1
            distintos += 1
            continue

        diffs = comparar_escenario(eid, data_a[eid], data_b[eid], unir_mensajes=args.unir_mensajes)
        if not diffs:
            print(f"[{eid}] IGUAL")
            iguales += 1
        else:
            print(f"[{eid}] DIFERENCIA:")
            for d in diffs:
                print(d)
            distintos += 1

    print("\n" + "=" * 50)
    print("=== RESUMEN COMPARACION ===")
    print(f"Total escenarios: {len(todos_los_ids)} | Iguales: {iguales} | Distintos: {distintos}")
    if faltantes > 0:
        print(f"Escenarios faltantes en alguno de los dos archivos: {faltantes}")
    print("=" * 50)

    if distintos > 0:
        sys.exit(1)
    else:
        sys.exit(0)


if __name__ == "__main__":
    main()
