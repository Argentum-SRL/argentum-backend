# -*- coding: utf-8 -*-
"""
scripts/demo/resumen.py — Generación y formateo de resúmenes de datos del usuario demo.
Incluye utilidades de forzado de fecha de creación y conteos de consistencia.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Dict

if TYPE_CHECKING:
    from scripts.demo.catalogo import CatalogoDemo
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.ajuste_saldo import AjusteSaldo
from app.models.billetera import Billetera
from app.models.cuota import Cuota
from app.models.grupo_cuotas import GrupoCuotas
from app.models.meta import Meta
from app.models.movimiento_meta import MovimientoMeta
from app.models.rendimiento_billetera import RendimientoBilletera
from app.models.transaccion import Transaccion
from app.models.transferencia_interna import TransferenciaInterna
from app.models.usuario import Usuario
from app.services import conciliacion_service, dashboard_service

logger = logging.getLogger(__name__)


def fecha_a_utc_12_argentina(f: date) -> datetime:
    """Retorna un datetime en UTC que corresponde exactamente a las 12:00 de Argentina (UTC-3 -> 15:00 UTC)."""
    return datetime(f.year, f.month, f.day, 15, 0, 0, tzinfo=timezone.utc)


def forzar_fechas_creacion(db: Session, usuario_id: UUID) -> None:
    """Corrige fecha_creacion para que sea su fecha a las 12:00 de Argentina."""
    for tx in db.execute(select(Transaccion).where(Transaccion.usuario_id == usuario_id)).scalars():
        tx.fecha_creacion = fecha_a_utc_12_argentina(tx.fecha)
    for tr in db.execute(select(TransferenciaInterna).where(TransferenciaInterna.usuario_id == usuario_id)).scalars():
        tr.fecha_creacion = fecha_a_utc_12_argentina(tr.fecha)
    for aj in db.execute(select(AjusteSaldo).join(Billetera, AjusteSaldo.billetera_id == Billetera.id).where(Billetera.usuario_id == usuario_id)).scalars():
        aj.fecha_creacion = fecha_a_utc_12_argentina(aj.fecha)
    for rb in db.execute(select(RendimientoBilletera).join(Billetera, RendimientoBilletera.billetera_id == Billetera.id).where(Billetera.usuario_id == usuario_id)).scalars():
        f = rb.fecha.date() if isinstance(rb.fecha, datetime) else rb.fecha
        rb.fecha_creacion = fecha_a_utc_12_argentina(f)
    for mm in db.execute(select(MovimientoMeta).join(Meta, MovimientoMeta.meta_id == Meta.id).where(Meta.usuario_id == usuario_id)).scalars():
        mm.fecha_creacion = fecha_a_utc_12_argentina(mm.fecha)


def construir_resumen(db: Session, usuario: Usuario, cat: CatalogoDemo, hoy: date) -> Dict[str, Any]:
    """Genera el resumen de cantidades por tipo, saldos teóricos vs actuales y disponible libre."""
    billeteras = [cat.b_galicia, cat.b_mp, cat.b_efectivo, cat.b_dolares, cat.b_balanz]
    saldos_info = {}
    for b in billeteras:
        db.refresh(b)
        s_act = (b.saldo_actual or Decimal("0.00")).quantize(Decimal("0.01"))
        s_teo = Decimal(str(conciliacion_service.calcular_saldo_teorico(db, b.id, hasta=hoy))).quantize(Decimal("0.01"))
        saldos_info[b.nombre] = {
            "moneda": b.moneda.value if hasattr(b.moneda, "value") else str(b.moneda),
            "saldo_actual": s_act,
            "saldo_teorico": s_teo,
            "iguales": (s_act == s_teo),
        }

    # Conteos por tipo de movimiento
    conteos = {
        "transacciones": db.execute(select(func.count(Transaccion.id)).where(Transaccion.usuario_id == usuario.id)).scalar() or 0,
        "transferencias": db.execute(select(func.count(TransferenciaInterna.id)).where(TransferenciaInterna.usuario_id == usuario.id)).scalar() or 0,
        "ajustes": db.execute(select(func.count(AjusteSaldo.id)).join(Billetera, AjusteSaldo.billetera_id == Billetera.id).where(Billetera.usuario_id == usuario.id)).scalar() or 0,
        "rendimientos": db.execute(select(func.count(RendimientoBilletera.id)).join(Billetera, RendimientoBilletera.billetera_id == Billetera.id).where(Billetera.usuario_id == usuario.id)).scalar() or 0,
        "movimientos_meta": db.execute(select(func.count(MovimientoMeta.id)).join(Meta, MovimientoMeta.meta_id == Meta.id).where(Meta.usuario_id == usuario.id)).scalar() or 0,
        "cuotas": db.execute(select(func.count(Cuota.id)).join(GrupoCuotas, Cuota.grupo_id == GrupoCuotas.id).where(GrupoCuotas.usuario_id == usuario.id)).scalar() or 0,
    }

    # Disponible libre de hoy vía dashboard_service
    disponible_libre_ars = Decimal("0.00")
    try:
        dash_res = dashboard_service.get_dashboard_resumen(db, usuario)
        if "disponible_real" in dash_res and "ars" in dash_res["disponible_real"]:
            disponible_libre_ars = Decimal(str(dash_res["disponible_real"]["ars"]["disponible"]))
    except Exception as e:
        logger.debug("Error al calcular disponible libre: %s", e)

    return {
        "usuario_email": usuario.email,
        "hoy": hoy.isoformat(),
        "conteos": conteos,
        "saldos": saldos_info,
        "disponible_libre_ars": disponible_libre_ars,
    }


def imprimir_resumen(resumen: Dict[str, Any]) -> None:
    """Imprime el resumen formateado por consola."""
    print("=" * 60)
    print(f"RESUMEN USUARIO DEMO ({resumen['usuario_email']}) - HOY: {resumen['hoy']}")
    print("=" * 60)
    print("CANTIDAD DE MOVIMIENTOS POR TIPO:")
    for tipo, cant in resumen["conteos"].items():
        print(f"  - {tipo.capitalize()}: {cant}")
    print("\nSALDOS POR BILLETERA (hasta hoy):")
    for nombre, s in resumen["saldos"].items():
        estado = "OK (IGUALES)" if s["iguales"] else "DISCREPANCIA"
        print(f"  - {nombre} ({s['moneda']}): Actual = {s['saldo_actual']} | Teórico = {s['saldo_teorico']} -> {estado}")
    print(f"\nDISPONIBLE LIBRE ARS: ${resumen['disponible_libre_ars']:,.2f}")
    print("=" * 60)

