# -*- coding: utf-8 -*-
"""
Script CLI para cargar, actualizar o borrar el usuario demo (Lucía Demo).

Uso:
  python -m scripts.demo.cargar_demo --crear [--hoy AAAA-MM-DD]
  python -m scripts.demo.cargar_demo --ponerse-al-dia [--hoy AAAA-MM-DD]
  python -m scripts.demo.cargar_demo --borrar [--confirmacion demo.argentum@argentum.com]
"""
from __future__ import annotations

import argparse
import getpass
import os
import sys
from datetime import date
from typing import Optional

from app.core.database import SessionLocal
from app.core.security import validar_reglas_password
from app.utils.fecha import hoy_argentina
from scripts.demo.generador import (
    ejecutar_borrar,
    ejecutar_crear,
    ejecutar_ponerse_al_dia,
    imprimir_resumen,
)
from scripts.demo.persona import EMAIL_DEMO


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Gestión del usuario demo Lucía Demo (demo.argentum@argentum.com)"
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--crear",
        action="store_true",
        help="Crea la cuenta demo y carga el plan hasta hoy y eventos futuros permitidos.",
    )
    group.add_argument(
        "--ponerse-al-dia",
        action="store_true",
        help="Carga vencimientos pasados que no podían fecharse a futuro.",
    )
    group.add_argument(
        "--borrar",
        action="store_true",
        help="Borra el usuario demo y todos sus registros asociados.",
    )

    parser.add_argument(
        "--hoy",
        type=str,
        default=None,
        help="Fecha de referencia simulada (AAAA-MM-DD). Por defecto: hoy real.",
    )
    parser.add_argument(
        "--confirmacion",
        type=str,
        default=None,
        help="Confirmación con email para borrado no interactivo.",
    )
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv)

    fecha_hoy: date = hoy_argentina()
    if args.hoy:
        try:
            fecha_hoy = date.fromisoformat(args.hoy)
        except ValueError:
            print(f"Error: formato de fecha inválido para --hoy: {args.hoy}. Use AAAA-MM-DD.")
            return 1

    db = SessionLocal()
    try:
        if args.crear:
            password = os.environ.get("DEMO_PASSWORD")
            if not password:
                password = getpass.getpass("Ingrese contraseña para el usuario demo: ")

            try:
                validar_reglas_password(password)
            except ValueError as e:
                print(f"Error: la contraseña no cumple las reglas de seguridad: {e}")
                return 1

            try:
                resumen = ejecutar_crear(db, password=password, hoy=fecha_hoy)
                imprimir_resumen(resumen)
                return 0
            except Exception as e:
                print(f"Error al crear usuario demo: {e}")
                return 1

        elif args.ponerse_al_dia:
            try:
                resumen = ejecutar_ponerse_al_dia(db, hoy=fecha_hoy)
                imprimir_resumen(resumen)
                return 0
            except Exception as e:
                print(f"Error al poner al día usuario demo: {e}")
                return 1

        elif args.borrar:
            confirmacion = args.confirmacion
            if not confirmacion:
                try:
                    confirmacion = input(f"Escribí {EMAIL_DEMO} para confirmar el borrado: ")
                except EOFError:
                    confirmacion = ""

            exito = ejecutar_borrar(db, confirmacion=confirmacion)
            return 0 if exito else 1

        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())

