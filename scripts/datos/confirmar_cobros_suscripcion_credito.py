# -*- coding: utf-8 -*-
"""
scripts/datos/confirmar_cobros_suscripcion_credito.py

Corrige transacciones de cobros de suscripción con tarjeta de crédito que quedaron
con estado_verificacion 'pendiente'. Las pasa a 'confirmada' para que sumen al gasto
del período y a presupuestos como compras en 1 pago en su fecha de cobro.

Modos:
  --simular (por defecto): solo lectura, resumen por usuario anónimo y totales por moneda.
  --aplicar: genera respaldo JSON en D:\\Argentum\\auditorias\\backups\\cobros_suscripcion_credito\\
             y actualiza a 'confirmada' en una sola transacción.
  --revertir <archivo.json>: vuelve a 'pendiente' exactamente las filas registradas en el archivo.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List

# Asegurar que el directorio raíz del backend esté en sys.path
_BACKEND_ROOT = str(Path(__file__).resolve().parent.parent.parent)
if _BACKEND_ROOT not in sys.path:
    sys.path.insert(0, _BACKEND_ROOT)

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.models.transaccion import (
    EstadoVerificacionTransaccion,
    MetodoPago,
    Transaccion,
)
from app.models.usuario import Usuario

BACKUP_DIR = Path(r"D:\Argentum\auditorias\backups\cobros_suscripcion_credito")


def buscar_transacciones_a_corregir(db: Session) -> List[Transaccion]:
    """Busca transacciones que son padres de cuotas de suscripción con tarjeta en estado pendiente."""
    stmt = (
        select(Transaccion)
        .where(
            Transaccion.suscripcion_id.isnot(None),
            Transaccion.es_padre_cuotas.is_(True),
            Transaccion.metodo_pago == MetodoPago.CREDITO,
            Transaccion.estado_verificacion == EstadoVerificacionTransaccion.PENDIENTE,
        )
        .order_by(Transaccion.usuario_id, Transaccion.fecha, Transaccion.id)
    )
    return list(db.execute(stmt).scalars().all())


def ejecutar_simulacion(db: Session) -> Dict[str, Any]:
    """Ejecuta en modo simulación (solo lectura). Devuelve dict con resumen y lo imprime."""
    txs = buscar_transacciones_a_corregir(db)
    print("=== SIMULACIÓN: Confirmación de cobros de suscripción con tarjeta ===")
    print(f"Total de transacciones encontradas: {len(txs)}")

    if not txs:
        print("No se encontraron transacciones pendientes de suscripción con tarjeta.")
        return {"total_transacciones": 0, "usuarios": {}, "totales_moneda": {}}

    # Mapeo anónimo de usuarios
    usuarios_cache: Dict[str, Usuario] = {}
    usuario_anonimo: Dict[str, str] = {}
    contador_anonimo = 1

    por_usuario: Dict[str, Dict[str, Any]] = {}
    totales_moneda: Dict[str, Decimal] = {}

    for tx in txs:
        uid_str = str(tx.usuario_id)
        if uid_str not in usuarios_cache:
            u = db.get(Usuario, tx.usuario_id)
            usuarios_cache[uid_str] = u
            if u and "testingadmin" in (u.email or "").lower():
                usuario_anonimo[uid_str] = "testingadmin"
            else:
                usuario_anonimo[uid_str] = f"Usuario_{contador_anonimo:02d}"
                contador_anonimo += 1

        label = usuario_anonimo[uid_str]
        if label not in por_usuario:
            por_usuario[label] = {"cantidad": 0, "montos": {}}

        por_usuario[label]["cantidad"] += 1
        mon_str = tx.moneda.value if hasattr(tx.moneda, "value") else str(tx.moneda)
        por_usuario[label]["montos"][mon_str] = (
            por_usuario[label]["montos"].get(mon_str, Decimal("0.00")) + tx.monto
        )
        totales_moneda[mon_str] = totales_moneda.get(mon_str, Decimal("0.00")) + tx.monto

    print("\nDesglose por usuario:")
    for label, info in sorted(por_usuario.items()):
        montos_txt = ", ".join(f"{m} {mon}" for mon, m in sorted(info["montos"].items()))
        print(f"  - {label}: {info['cantidad']} transacciones ({montos_txt})")

    print("\nTotales por moneda:")
    for mon, total in sorted(totales_moneda.items()):
        print(f"  - {mon}: {total}")

    print("=====================================================================")
    return {
        "total_transacciones": len(txs),
        "usuarios": {k: {"cantidad": v["cantidad"], "montos": {m: str(val) for m, val in v["montos"].items()}} for k, v in por_usuario.items()},
        "totales_moneda": {k: str(v) for k, v in totales_moneda.items()},
    }


def ejecutar_aplicar(db: Session, backup_path: Path | None = None) -> Path:
    """Guarda respaldo y pasa las filas a confirmada en una sola transacción."""
    txs = buscar_transacciones_a_corregir(db)
    if not txs:
        print("No hay transacciones pendientes para confirmar.")
        return Path()

    if backup_path is None:
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        now_str = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = BACKUP_DIR / f"{now_str}.json"
    else:
        backup_path.parent.mkdir(parents=True, exist_ok=True)

    backup_data = [
        {
            "id": str(tx.id),
            "usuario_id": str(tx.usuario_id),
            "estado_anterior": tx.estado_verificacion.value if hasattr(tx.estado_verificacion, "value") else str(tx.estado_verificacion),
            "monto": str(tx.monto),
            "moneda": tx.moneda.value if hasattr(tx.moneda, "value") else str(tx.moneda),
            "fecha": tx.fecha.isoformat(),
            "descripcion": tx.descripcion,
        }
        for tx in txs
    ]

    with open(backup_path, "w", encoding="utf-8") as f:
        json.dump(backup_data, f, indent=2, ensure_ascii=False)

    print(f"Respaldo guardado en: {backup_path}")

    # Actualizar a confirmada en una sola transacción
    for tx in txs:
        tx.estado_verificacion = EstadoVerificacionTransaccion.CONFIRMADA

    db.commit()
    print(f"Se actualizaron {len(txs)} transacciones a 'confirmada' exitosamente.")
    return backup_path


def ejecutar_revertir(db: Session, archivo_json: Path | str) -> int:
    """Revierte exactamente las filas guardadas en archivo_json a 'pendiente'."""
    path = Path(archivo_json)
    if not path.is_file():
        raise FileNotFoundError(f"No existe el archivo de respaldo: {path}")

    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    if not isinstance(data, list):
        raise ValueError("El archivo JSON debe contener una lista de transacciones.")

    revertidas = 0
    for item in data:
        tx_id = item.get("id")
        if not tx_id:
            continue
        tx = db.get(Transaccion, tx_id)
        if tx:
            tx.estado_verificacion = EstadoVerificacionTransaccion.PENDIENTE
            revertidas += 1

    db.commit()
    print(f"Se revirtieron {revertidas} transacciones a 'pendiente' exitosamente.")
    return revertidas


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Corrige cobros de suscripciones con tarjeta a estado 'confirmada'."
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--simular", action="store_true", default=True, help="Solo lectura (por defecto)")
    group.add_argument("--aplicar", action="store_true", help="Aplica el cambio guardando respaldo JSON previo")
    group.add_argument("--revertir", type=str, metavar="ARCHIVO_JSON", help="Revierte las transacciones del respaldo a 'pendiente'")

    args = parser.parse_args()

    db = SessionLocal()
    try:
        if args.revertir:
            ejecutar_revertir(db, args.revertir)
        elif args.aplicar:
            ejecutar_aplicar(db)
        else:
            ejecutar_simulacion(db)
    finally:
        db.close()


if __name__ == "__main__":
    main()

