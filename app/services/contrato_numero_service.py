# -*- coding: utf-8 -*-
"""
contrato_numero_service.py - Base del contrato de número para Disponible libre.

Declara valor, base, cobertura, frescura y supuestos desde una sola fuente,
garantizando consistencia matemática y conceptual entre la web y WhatsApp.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.billetera import Billetera, EstadoBilletera
from app.models.usuario import Usuario
from app.services import ajuste_saldo_service
from app.services.dashboard_service import get_ciclo_fechas
from app.services.frescura_service import Frescura


@dataclass(frozen=True)
class ContratoNumero:
    """
    Representa el contrato de un número financiero (ej. Disponible libre).
    """
    valor: Decimal
    moneda: str
    base_desde: date
    base_hasta: date
    fecha_corte: date
    cobertura: int | None
    frescura: Frescura
    supuestos: tuple[str, ...]

    def a_dict(self) -> dict[str, Any]:
        """Convierte el contrato a diccionario con tipos serializables."""
        return {
            "valor": self.valor,
            "moneda": self.moneda,
            "base_desde": self.base_desde.isoformat(),
            "base_hasta": self.base_hasta.isoformat(),
            "fecha_corte": self.fecha_corte.isoformat(),
            "cobertura": self.cobertura,
            "frescura": self.frescura.a_dict(),
            "supuestos": list(self.supuestos),
        }


def cobertura_usuario(db: Session, usuario_id: UUID, hoy: date) -> int | None:
    """
    Calcula la cobertura del usuario: el menor por_cada_100 de ajuste_saldo_service.calcular_cobertura
    entre sus billeteras activas que no son de inversión y tienen mostrar True;
    None si ninguna cumple la condición.
    """
    stmt = select(Billetera).where(
        Billetera.usuario_id == usuario_id,
        Billetera.estado == EstadoBilletera.ACTIVA,
        Billetera.es_inversion == False,
    )
    billeteras = db.execute(stmt).scalars().all()

    porcentajes: list[int] = []
    for b in billeteras:
        cob = ajuste_saldo_service.calcular_cobertura(db, b.id, hoy=hoy)
        if cob.get("mostrar") and cob.get("por_cada_100") is not None:
            porcentajes.append(cob["por_cada_100"])

    return min(porcentajes) if porcentajes else None


def contrato_disponible_libre(
    db: Session,
    usuario: Usuario,
    hoy: date,
    disponible: dict,
    frescura: Frescura,
) -> dict[str, ContratoNumero]:
    """
    Genera el contrato de número para el Disponible libre en 'ars' y 'usd'.
    Reutiliza el diccionario de disponible ya calculado sin recalcular ni consultar próximos pagos.
    """
    base_desde, base_hasta = get_ciclo_fechas(usuario, hoy)
    cobertura = cobertura_usuario(db, usuario.id, hoy)
    supuestos = (
        "Suma solo billeteras activas que no son de inversión.",
        f"Descuenta lo que vence hasta el {base_hasta:%d/%m}.",
    )

    contratos: dict[str, ContratoNumero] = {}
    for mon in ("ars", "usd"):
        monto_raw = disponible.get(mon, {}).get("saldo_disponible", Decimal("0"))
        valor = Decimal(str(monto_raw))
        contratos[mon] = ContratoNumero(
            valor=valor,
            moneda=mon.upper(),
            base_desde=base_desde,
            base_hasta=base_hasta,
            fecha_corte=hoy,
            cobertura=cobertura,
            frescura=frescura,
            supuestos=supuestos,
        )

    return contratos

