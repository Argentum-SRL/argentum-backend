"""
Script para cargar el histórico de cotizaciones del dólar en la base de datos de Argentum.

FORMATO REAL DE LA API https://api.argentinadatos.com/v1/cotizaciones/dolares:
La API devuelve una lista de objetos JSON con el siguiente formato:
[
  {
    "casa": "oficial",
    "compra": 1485,
    "venta": 1535,
    "fecha": "2026-09-02"
  },
  {
    "casa": "blue",
    "compra": 1525,
    "venta": 1545,
    "fecha": "2026-09-02"
  },
  {
    "casa": "bolsa",
    "compra": 1528,
    "venta": 1533.9,
    "fecha": "2026-09-02"
  },
  {
    "casa": "tarjeta",
    "compra": 1930.5,
    "venta": 1995.5,
    "fecha": "2026-09-02"
  }
]

Donde:
- "casa": string que identifica el tipo de cotización ("oficial", "blue", "bolsa", "tarjeta", etc.)
- "compra": float | int | null
- "venta": float | int | null
- "fecha": string con fecha en formato "YYYY-MM-DD"

Mapeo al sistema de Argentum:
- "oficial" -> "oficial"
- "blue"    -> "blue"
- "bolsa"   -> "mep"
- "tarjeta" -> "tarjeta"
"""
from __future__ import annotations

import sys
from collections import defaultdict
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

import httpx
from sqlalchemy import select

from app.core.database import SessionLocal
from app.models.cotizacion_dolar import CotizacionDolar
from app.utils.fecha import hoy_argentina

FECHA_DESDE = date(2026, 1, 1)

CASA_MAP = {
    "oficial": "oficial",
    "blue": "blue",
    "bolsa": "mep",
    "mep": "mep",
    "tarjeta": "tarjeta",
}

API_URL_UNIFICADA = "https://api.argentinadatos.com/v1/cotizaciones/dolares"
API_URL_CASA = "https://api.argentinadatos.com/v1/cotizaciones/dolares/{casa}"


def obtener_datos_historicos() -> list[dict[str, Any]]:
    """
    Obtiene las cotizaciones históricas desde argentinadatos.com.
    Intenta primero la URL unificada y como fallback consulta casa por casa.
    """
    registros: list[dict[str, Any]] = []

    try:
        with httpx.Client(timeout=30.0) as client:
            resp = client.get(API_URL_UNIFICADA)
            if resp.status_code == 200:
                data = resp.json()
                if isinstance(data, list):
                    return data
    except Exception as exc:
        print(f"[WARN] Error al consultar endpoint unificado: {exc}. Reintentando por casa...")

    # Fallback por casa individual
    casas_a_consultar = ["oficial", "blue", "bolsa", "tarjeta"]
    with httpx.Client(timeout=20.0) as client:
        for c in casas_a_consultar:
            try:
                url = API_URL_CASA.format(casa=c)
                resp = client.get(url)
                if resp.status_code == 200:
                    data = resp.json()
                    if isinstance(data, list):
                        registros.extend(data)
            except Exception as e:
                print(f"[ERROR] No se pudo obtener datos para casa '{c}': {e}")

    return registros


def cargar_historico() -> None:
    """
    Carga e idempotentemente sincroniza cotizaciones históricas del dólar
    desde el 1 de enero de 2026 hasta hoy en la tabla cotizaciones_dolar.
    """
    fecha_hasta = hoy_argentina()
    print(f"Iniciando carga de histórico de cotizaciones ({FECHA_DESDE} a {fecha_hasta})...")

    raw_items = obtener_datos_historicos()
    if not raw_items:
        print("[ERROR] No se recibieron datos de la API de cotizaciones.")
        return

    insertados: dict[str, int] = defaultdict(int)
    actualizados: dict[str, int] = defaultdict(int)

    db = SessionLocal()
    try:
        for item in raw_items:
            casa_raw = str(item.get("casa", "")).lower().strip()
            if casa_raw not in CASA_MAP:
                continue

            tipo_sistema = CASA_MAP[casa_raw]

            fecha_str = item.get("fecha")
            if not fecha_str:
                continue

            try:
                fecha_reg = datetime.strptime(str(fecha_str), "%Y-%m-%d").date()
            except ValueError:
                continue

            # Filtrar rango: desde 2026-01-01 hasta hoy
            if fecha_reg < FECHA_DESDE or fecha_reg > fecha_hasta:
                continue

            compra_raw = item.get("compra")
            venta_raw = item.get("venta")

            if compra_raw is None and venta_raw is None:
                continue

            compra = Decimal(str(compra_raw)) if compra_raw is not None else None
            venta = Decimal(str(venta_raw)) if venta_raw is not None else None

            if compra is None:
                compra = venta
            if venta is None:
                venta = compra

            promedio = ((compra + venta) / Decimal("2")).quantize(Decimal("0.0001"))

            # Idempotencia: Verificar si existe el par (fecha, tipo)
            stmt = select(CotizacionDolar).where(
                CotizacionDolar.fecha == fecha_reg,
                CotizacionDolar.tipo == tipo_sistema,
            )
            existente = db.execute(stmt).scalar_one_or_none()

            if existente:
                # Comprobar si hubo cambios antes de actualizar
                if (
                    existente.compra != compra
                    or existente.venta != venta
                    or existente.promedio != promedio
                ):
                    existente.compra = compra
                    existente.venta = venta
                    existente.promedio = promedio
                    existente.fecha_registro = datetime.now(timezone.utc)
                    actualizados[tipo_sistema] += 1
            else:
                nuevo = CotizacionDolar(
                    fecha=fecha_reg,
                    tipo=tipo_sistema,
                    compra=compra,
                    venta=venta,
                    promedio=promedio,
                    fecha_registro=datetime.now(timezone.utc),
                )
                db.add(nuevo)
                insertados[tipo_sistema] += 1

        db.commit()

        print("\n=== RESUMEN DE CARGA HISTÓRICA DE COTIZACIONES ===")
        tipos_esperados = ["oficial", "blue", "tarjeta", "mep"]
        for t in tipos_esperados:
            ins = insertados.get(t, 0)
            act = actualizados.get(t, 0)
            print(f"Tipo '{t}': {ins} registros insertados, {act} actualizados.")
        print("Carga histórica finalizada con éxito.")

    except Exception as exc:
        db.rollback()
        print(f"[FATAL] Error durante la carga histórica: {exc}")
        raise
    finally:
        db.close()


if __name__ == "__main__":
    cargar_historico()
