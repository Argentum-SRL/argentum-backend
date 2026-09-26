"""
Herramienta de Auditoría y Comparación: Comparador de Fotos del Motor de Argentum.

Propósito:
Compara dos archivos JSON producidos por foto_motor.py e identifica:
1. Claves nuevas presentes en la foto B que no estaban en la foto A.
2. Claves que desaparecieron (estaban en A pero no en B).
3. Claves con valores modificados, calculando la diferencia absoluta y porcentual
   cuando los valores son cuantitativos/monetarios.

Uso:
  python scripts/motor/comparar_fotos.py <foto_a.json> <foto_b.json> [--ignorar-metadatos]

Código de salida:
  0 si no hay diferencias (o solo en metadatos con --ignorar-metadatos).
  1 si se detectan diferencias financieras o estructurales.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from decimal import Decimal, InvalidOperation
from typing import Any


METADATOS_FOTO = {
    "foto.fecha_hora_iso",
    "foto.fecha_hora_legible",
    "foto.nombre_archivo",
    "foto.etiqueta",
    "foto.archivo_json",
    "foto.archivo_txt",
}


def _es_numerico(val: Any) -> tuple[bool, Decimal | None]:
    """Intenta parsear un valor a Decimal."""
    if val is None or isinstance(val, bool):
        return False, None
    try:
        d = Decimal(str(val).strip())
        return True, d
    except (InvalidOperation, ValueError, TypeError):
        return False, None


def cargar_foto(ruta: str) -> dict[str, Any]:
    """Carga y valida un archivo JSON de foto del motor."""
    if not os.path.exists(ruta):
        raise FileNotFoundError(f"El archivo no existe: {ruta}")
    with open(ruta, "r", encoding="utf-8") as f:
        datos = json.load(f)
    if not isinstance(datos, dict):
        raise ValueError(f"El archivo {ruta} no contiene un objeto JSON válido.")
    return datos


def comparar_fotos(
    foto_a: dict[str, Any],
    foto_b: dict[str, Any],
    ignorar_metadatos: bool = False,
) -> tuple[list[str], list[str], list[dict[str, Any]], bool]:
    """
    Compara dos diccionarios de fotos.
    Devuelve (claves_nuevas, claves_desaparecidas, diferencias_valores, son_iguales).
    """
    keys_a = set(foto_a.keys())
    keys_b = set(foto_b.keys())

    if ignorar_metadatos:
        keys_a = {k for k in keys_a if k not in METADATOS_FOTO and not k.startswith("foto.")}
        keys_b = {k for k in keys_b if k not in METADATOS_FOTO and not k.startswith("foto.")}

    claves_nuevas = sorted(list(keys_b - keys_a))
    claves_desaparecidas = sorted(list(keys_a - keys_b))

    claves_comunes = sorted(list(keys_a & keys_b))
    diferencias_valores: list[dict[str, Any]] = []

    for k in claves_comunes:
        val_a = str(foto_a[k]).strip()
        val_b = str(foto_b[k]).strip()

        if val_a != val_b:
            # Calcular diferencias cuantitativas si aplica
            es_num_a, num_a = _es_numerico(val_a)
            es_num_b, num_b = _es_numerico(val_b)

            diff_monto = None
            diff_pct = None

            if es_num_a and es_num_b and num_a is not None and num_b is not None:
                diff_monto = num_b - num_a
                if num_a != Decimal("0"):
                    diff_pct = ((num_b - num_a) / abs(num_a)) * Decimal("100")

            diferencias_valores.append({
                "clave": k,
                "antes": val_a,
                "despues": val_b,
                "diff_monto": diff_monto,
                "diff_pct": diff_pct,
            })

    son_iguales = (
        len(claves_nuevas) == 0
        and len(claves_desaparecidas) == 0
        and len(diferencias_valores) == 0
    )
    return claves_nuevas, claves_desaparecidas, diferencias_valores, son_iguales


def imprimir_reporte_comparacion(
    ruta_a: str,
    ruta_b: str,
    claves_nuevas: list[str],
    claves_desaparecidas: list[str],
    diferencias: list[dict[str, Any]],
    son_iguales: bool,
    ignorar_metadatos: bool = False,
) -> None:
    """Imprime en consola el reporte formateado de la comparación."""
    print("=" * 80)
    print("COMPARACION DE FOTOS DEL MOTOR DE ARGENTUM")
    print("=" * 80)
    print(f"Foto A (Antes):   {ruta_a}")
    print(f"Foto B (Después): {ruta_b}")
    if ignorar_metadatos:
        print("Modo: Ignorando metadatos de ejecución (hora y nombres de archivo)")
    print("-" * 80)

    if son_iguales:
        print("\nRESULTADO: LAS FOTOS SON EXACTAMENTE IGUALES.")
        print("No se encontraron diferencias en las métricas calculadas.")
        print("=" * 80)
        return

    print("\nRESULTADO: SE ENCONTRARON DIFERENCIAS.")

    if claves_nuevas:
        print(f"\n[+] CLAVES NUEVAS ({len(claves_nuevas)}):")
        for k in claves_nuevas:
            print(f"    + {k}")

    if claves_desaparecidas:
        print(f"\n[-] CLAVES DESAPARECIDAS ({len(claves_desaparecidas)}):")
        for k in claves_desaparecidas:
            print(f"    - {k}")

    if diferencias:
        print(f"\n[*] VALORES MODIFICADOS ({len(diferencias)}):")
        for d in diferencias:
            k = d["clave"]
            antes = d["antes"]
            despues = d["despues"]
            monto_info = ""
            if d["diff_monto"] is not None:
                signo = "+" if d["diff_monto"] > 0 else ""
                monto_info = f" | Dif: {signo}{float(d['diff_monto']):.2f}"
                if d["diff_pct"] is not None:
                    monto_info += f" ({signo}{float(d['diff_pct']):.2f}%)"
            print(f"    * {k}:")
            print(f"        Antes:   {antes}")
            print(f"        Después: {despues}{monto_info}")

    print("\n" + "=" * 80)


def main():
    parser = argparse.ArgumentParser(
        description="Comparador de fotos del motor financiero de Argentum."
    )
    parser.add_argument("foto_a", type=str, help="Ruta al archivo JSON de la foto A (referencia/antes).")
    parser.add_argument("foto_b", type=str, help="Ruta al archivo JSON de la foto B (posterior/después).")
    parser.add_argument(
        "--ignorar-metadatos",
        action="store_true",
        help="Ignora campos de metadatos de foto (fecha, hora, nombre de archivo).",
    )
    args = parser.parse_args()

    try:
        foto_a = cargar_foto(args.foto_a)
        foto_b = cargar_foto(args.foto_b)

        nuevas, desaparecidas, diferencias, son_iguales = comparar_fotos(
            foto_a, foto_b, ignorar_metadatos=args.ignorar_metadatos
        )

        imprimir_reporte_comparacion(
            args.foto_a,
            args.foto_b,
            nuevas,
            desaparecidas,
            diferencias,
            son_iguales,
            ignorar_metadatos=args.ignorar_metadatos,
        )

        if son_iguales:
            sys.exit(0)
        else:
            sys.exit(1)

    except Exception as e:
        print(f"ERROR: {type(e).__name__}: {str(e)}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
